"""CR 603.6d/614.12/707.2: copied static entry components, not source counters."""
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.runtime_templates import static_runtime_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from quorune.session import CommanderSession
from quorune.counter_placement import CounterPlacementError, validate_counter_event_subjects
from quorune.replacement_effects import AffectedObject, ReplaceableEvent
from quorune.errors import GameRuleError
from scripts.build_test_database import build_fixture_database

ROOT = Path(__file__).resolve().parents[1]


class CopiedSelfEntryCounterCompilerTests(unittest.TestCase):
    def test_fixed_self_entry_component_uses_the_shared_presence_marker(self):
        card = CardRecord(oracle_id="fixture:fixed-entry-component", name="Generic Entry Component",
            mana_cost="{W}", mana_value=1, type_line="Creature — Wizard",
            oracle_text="This creature enters with two +1/+1 counters on it.",
            power="2", toughness="2", loyalty=None, defense=None,
            colors=("W",), color_identity=("W",), keywords=(), produced_mana=(),
            layout="normal", released_at="2026-01-01", legalities={"commander":"legal"},
            faces=(), raw={})
        registry = load_default_capability_registry()
        ir = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        plan = static_runtime_template(card.oracle_text, source_name=card.name)
        self.assertIsNotNone(plan)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, plan.runtime_coverage)


class CopiedSelfEntryCounterActionTests(unittest.TestCase):
    from test_token_entry_boundaries import TokenEntryActionReproductions as _helpers
    session = _helpers.session
    add = _helpers.add
    register = _helpers.register
    seal = _helpers.seal
    cast = _helpers.cast
    finish = _helpers.finish
    order_triggers = _helpers.order_triggers
    replay = _helpers.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "copied-counter.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/linked-exile-return-cards.json",
            ROOT / "tests/fixtures/token-entry-boundary-cards.json",
            ROOT / "tests/fixtures/copied-self-entry-counter-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.deck = DeckDefinition("Generic copied counter", [
            DeckEntry("Generic Blink Commander", 1, "commander"),
            DeckEntry("Generic Blink Plains", 15),
        ], ["Generic Blink Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_actual_trusted_copy_gets_its_own_entry_counters_and_replays(self):
        session = self.session(25510001)
        self.register(session, ("Generic Copied Counter Creature", "Generic Entry One Copy"))
        original = self.add(session, "Generic Copied Counter Creature", "ORIGINAL", zone="battlefield")
        original.counters["+1/+1"] = 7
        spell = self.add(session, "Generic Entry One Copy", "COPY")
        self.seal(session)
        self.cast(session, spell, [original.ref])
        self.finish(session)
        token = next(card for card in session.state.cards.values() if card.is_token)
        actual = token.counters.get("+1/+1", 0)
        self.assertEqual(7, session.state.cards[original.object_id].counters["+1/+1"])
        self.replay(session)

        self.assertEqual(2, actual, "The copy gets fresh entry counters, not zero and not the source's seven")
    def test_actual_source_only_ability_removal_is_not_copied(self):
        session = self.session(25510002)
        source_name = "Generic Copied Counter Creature"
        removal_name = "Generic Copied Counter Source Transformation"
        self.register(session, (source_name, removal_name, "Generic Entry One Copy"))
        source = self.add(session, source_name, "ORIGINAL", zone="battlefield")
        removal = self.add(session, removal_name, "REMOVAL")
        copy = self.add(session, "Generic Entry One Copy", "COPY")
        self.seal(session)
        self.cast(session, removal, [source.ref])
        self.finish(session)
        source = session.state.cards[source.object_id]
        self.assertEqual((), session.engine._effective_static_component_keys(source))
        self.assertTrue(session.engine._copyable_characteristics(source)["ability_fragments"])
        self.cast(session, copy, [source.ref])
        self.finish(session)
        token = next(card for card in session.state.cards.values() if card.is_token)
        self.assertEqual(2, token.counters.get("+1/+1"))
        self.assertIn("vigilance", session.engine._combat_keywords(token))
        self.assertNotIn("vigilance", session.engine._combat_keywords(source))
        self.replay(session)

    def test_actual_copied_group_counter_replacement_choice_is_atomic_and_replays(self):
        session = self.session(25510003)
        source_name = "Generic Copied Counter Creature"
        names = (source_name, "Generic Entry Two Copies", "Generic Entry Counter Double", "Generic Entry Counter Add")
        self.register(session, names)
        source = self.add(session, source_name, "ORIGINAL", owner="B", controller="A", zone="battlefield")
        for index, name in enumerate(names[2:]):
            self.add(session, name, f"REPLACEMENT{index}", zone="battlefield")
        spell = self.add(session, "Generic Entry Two Copies", "COPY")
        self.seal(session)
        self.cast(session, spell, [source.ref])
        for _ in range(20):
            if session.state.pending_decision.kind == "replacement.order":
                break
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        else:
            self.fail("Copied entry-counter replacement choice was not offered")
        decisions = 0
        while session.state.pending_decision.kind == "replacement.order":
            decisions += 1
            self.assertLess(decisions, 8)
            self.assertFalse(any(card.is_token for card in session.state.cards.values()))
            for seat in "BCD":
                self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "choice"
                session.save(path)
                loaded = CommanderSession.load(self.db, path)
            self.assertEqual(authoritative_state_hash(session.state), authoritative_state_hash(loaded.state))
            for principal in ("pilot:A", "pilot:B"):
                before = authoritative_state_hash(loaded.state)
                rejected = loaded.act(principal, {"action_id": "choose", "replacement": "invalid"})
                self.assertFalse(rejected.ok)
                self.assertEqual(before, authoritative_state_hash(loaded.state))
            decision = loaded.packet("pilot:A", full=True)["decision"]
            self.assertNotIn("object_id", str(decision))
            option = decision["ctx"]["options"][0]["id"]
            chosen = loaded.act("pilot:A", {"action_id": "choose", "replacement": option})
            self.assertTrue(chosen.ok, chosen.summary)
            session = loaded
        self.finish(session)
        tokens = [card for card in session.state.cards.values() if card.is_token]
        self.assertEqual(2, len(tokens))
        self.assertTrue(all(card.owner == "A" and card.controller == "A" for card in tokens))
        self.assertTrue(all(card.counters.get("+1/+1") in {5, 6} for card in tokens))
        self.assertFalse(session.state.cards[source.object_id].counters)
        rows = [row for row in session.state.turn_history.events if row.kind == "permanent_entered"]
        self.assertEqual({card.logical_object_id for card in tokens}, {row.object_incarnation for row in rows})
        self.assertEqual(2, len(rows))
        self.replay(session)

    def test_copy_does_not_inherit_the_original_cast_x_or_existing_counters(self):
        session = self.session(25510004)
        source_name = "Generic Copied Counter X Creature"
        self.register(session, (source_name, "Generic Entry One Copy"))
        source = self.add(session, source_name, "ORIGINAL", zone="battlefield")
        source.counters["+1/+1"] = 5
        spell = self.add(session, "Generic Entry One Copy", "COPY")
        self.seal(session)
        self.cast(session, spell, [source.ref])
        self.finish(session)
        token = next(card for card in session.state.cards.values() if card.is_token)
        self.assertEqual(0, token.counters.get("+1/+1", 0))
        self.assertEqual(5, session.state.cards[source.object_id].counters["+1/+1"])
        self.replay(session)

    def test_unavailable_prospective_component_query_rejects_resolution_atomically(self):
        session = self.session(25510005)
        self.register(session, ("Generic Copied Counter Creature", "Generic Entry One Copy"))
        source = self.add(session, "Generic Copied Counter Creature", "ORIGINAL", zone="battlefield")
        spell = self.add(session, "Generic Entry One Copy", "COPY")
        self.seal(session)
        self.cast(session, spell, [source.ref])
        original_query = session.engine._effective_static_component_keys
        def unavailable(card, **kwargs):
            if card.is_token and kwargs.get("prospective_zone") == "battlefield":
                raise GameRuleError("Prospective component query unavailable")
            return original_query(card, **kwargs)
        rejected = None
        with patch.object(session.engine, "_effective_static_component_keys", side_effect=unavailable):
            for _ in range(12):
                before = authoritative_state_hash(session.state)
                principal = session.pending_principals()[0]
                result = session.act(principal, {"action_id": "pass"})
                if not result.ok:
                    rejected = result
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    break
        self.assertIsNotNone(rejected)
        self.assertIn("unavailable", rejected.summary.casefold())
        self.assertFalse(any(card.is_token for card in session.state.cards.values()))
        self.assertFalse([row for row in session.state.turn_history.events if row.kind == "permanent_entered"])
        self.finish(session)
        token = next(card for card in session.state.cards.values() if card.is_token)
        self.assertEqual(2, token.counters.get("+1/+1"))
        self.replay(session)


class CopiedEntryCounterIdentityTests(unittest.TestCase):
    def prospective_tree(self, *, child_identity="future@0", parent_marker=True):
        affected = AffectedObject(object_id="future", owner="A", controller="A")
        child = ReplaceableEvent(event_id="counter:future", kind="counter.place",
            affected_player=None, affected_object=affected,
            payload={"target_zone":"battlefield", "target_logical_object_id":child_identity,
                     "prospective_subject":True, "follows_zone_destination":True})
        parent = ReplaceableEvent(event_id="zone:future", kind="zone.change",
            affected_player=None, affected_object=affected,
            payload={"origin":"outside", "destination":"battlefield",
                     "logical_object_id":"future@0", "prospective_subject":parent_marker},
            children=(child,))
        return SimpleNamespace(state=SimpleNamespace(cards={})), parent

    def test_matching_prospective_parent_and_child_validate_without_state_insertion(self):
        host, parent = self.prospective_tree()
        validate_counter_event_subjects(host, (parent,))
        self.assertEqual({}, host.state.cards)

    def test_prospective_counter_child_cannot_substitute_another_incarnation(self):
        host, parent = self.prospective_tree(child_identity="future@2")
        with self.assertRaises(CounterPlacementError):
            validate_counter_event_subjects(host, (parent,))

    def test_prospective_counter_parent_marker_does_not_accept_a_boolean_alias(self):
        host, parent = self.prospective_tree(parent_marker=1)
        with self.assertRaises(CounterPlacementError):
            validate_counter_event_subjects(host, (parent,))
