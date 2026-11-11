"""Witnesses for mandatory controller-selected returns at resolution."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import special_trigger_fixture as turn_fixtures
from test_granted_skulk import record
from quorune.carddb import CardDatabase
from quorune.compiler import fixed_entry_return_requirements as return_owner
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


UPKEEP = "At the beginning of your upkeep, return a green creature you control to its owner's hand."
ENTRY = "When this creature enters, return a blue or black creature you control to its owner's hand."


class ControlledReturnCompilerTests(unittest.TestCase):
    def test_printed_schedule_and_color_union_require_the_closed_choice(self):
        registry = load_default_capability_registry()

        def trigger(text):
            ir = compile_oracle_card(record(text, "Creature"), capability_registry=registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertIn(return_owner.FIXED_ENTRY_RETURN_CAPABILITY, node.capability_dependencies)
            self.assertIsNone(node.target_schema)
            effect, = node.effects
            self.assertEqual(("choose_cards_apnap", "$controller", ["$controller"], 1, "return_owner_hand"),
                             tuple(effect[key] for key in ("op", "actor", "players", "count", "then")))
            return node, effect["predicate"]

        upkeep, predicate = trigger(UPKEEP)
        self.assertEqual("step.begin", upkeep.event)
        self.assertIn({"field": "player", "op": "eq", "value": "$source.controller"}, upkeep.event_condition["all"])
        self.assertEqual(("G",), tuple(predicate["colors_any"]))

        def witness():
            entry, predicate = trigger(ENTRY)
            self.assertEqual("permanent.enter", entry.event)
            self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, entry.event_condition)
            self.assertEqual({"U", "B"}, set(predicate["colors_any"]))

        witness()
        original = return_owner._controlled_quality_query

        def drop_one_color(quality, *, excludes_source):
            query = original(quality, excludes_source=excludes_source)
            if query is not None and set(query.colors_any) == {"U", "B"}:
                return replace(query, colors_any=("U",))
            return query

        with patch.object(return_owner, "_controlled_quality_query", side_effect=drop_one_color):
            with self.assertRaises(AssertionError):
                witness()

        for promoted in (
            UPKEEP.replace("return a", "you may return a"),
            UPKEEP.replace("return a green creature", "return another green creature"),
            UPKEEP.replace("return a green creature", "return up to two green creatures"),
        ):
            with self.subTest(promoted=promoted):
                ir = compile_oracle_card(record(promoted, "Creature"), capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
        for unsupported in (
            UPKEEP.replace("you control", "an opponent controls"),
            UPKEEP + " Rewrite history.",
        ):
            with self.subTest(unsupported=unsupported):
                ir = compile_oracle_card(record(unsupported, "Creature"), capability_registry=registry)
                self.assertNotEqual("exact", ir.status)


class ControlledReturnNativeTests(InheritedNativeFixture, unittest.TestCase):
    begin_prepared_turn = turn_fixtures.SpecialTriggerFixture.begin_prepared_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-return.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/controlled-return-choice-cards.json",
            ROOT / "tests/fixtures/attacking-card-entry-cards.json",
            ROOT / "tests/fixtures/bound-static-declaration-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Controlled return",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def upkeep(self, session, seat):
        self.begin_prepared_turn(session, seat)
        self.until(session, lambda game: game.state.step == "upkeep" and game.state.pending_decision is not None)

    def choice(self, session, eligible):
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual(("choice.apnap", ["A"]), (decision.kind, list(decision.actors)))
        ctx = session.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual(1, ctx["count"])
        self.assertEqual(set(eligible), set(ctx["options"]))
        for seat in "BCD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])

    def reject(self, session, seat, ref):
        before = authoritative_state_hash(session.state)
        result = session.act(f"pilot:{seat}", {"action_id": "choose", "cards": [ref]})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_actual_wildebeests_requires_its_controllers_upkeep_and_can_return_itself(self):
        session = self.session(608217101)
        source = self.add(session.engine, "Stampeding Wildebeests")
        wrong_color = self.add(session.engine, "Wind Drake")
        other_seat = self.add(session.engine, "Grizzly Bears", seat="B")
        self.assert_closed(session, source)
        self.prepare(session)
        self.upkeep(session, "B")
        self.assertEqual(0, self.trigger_count(session, source))
        self.upkeep(session, "A")
        self.assertEqual(1, self.trigger_count(session, source))
        self.assertEqual("priority", session.state.pending_decision.kind)
        self.assertFalse(session.state.stack[-1].targets)
        self.choice(session, [source.ref])
        self.reject(session, "B", source.ref)
        self.reject(session, "A", wrong_color.ref)
        self.reject(session, "A", other_seat.ref)
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "cards": [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_actual_cavern_harpy_offers_either_color_and_uses_the_owners_hand(self):
        session = self.session(608217102)
        source = self.add(session.engine, "Cavern Harpy", zone="hand")
        blue = self.add(session.engine, "Wind Drake", seat="B")
        session.engine.change_control(blue.object_id, "A", reason="controller selection fixture")
        black = self.add(session.engine, "Typhoid Rats")
        other = self.add(session.engine, "Grizzly Bears")
        private = self.add(session.engine, "Twiddle", seat="D", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session, mana={"U": 1, "B": 1})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.resolve_top(session)
        self.assertEqual(1, self.trigger_count(session, source))
        self.assertFalse(session.state.stack[-1].targets)
        self.choice(session, [source.ref, blue.ref, black.ref])
        self.reject(session, "A", other.ref)
        self.reject(session, "B", blue.ref)
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "cards": [blue.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIn(blue.object_id, session.state.players["B"].zones["hand"])
        self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_actual_riptide_chimera_resolves_with_no_choice_after_its_source_leaves(self):
        session = self.session(608217103)
        source = self.add(session.engine, "Riptide Chimera")
        foreign = self.add(session.engine, "Riptide Chimera", seat="B", ref="other-seat-enchantment")
        removal = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        self.add(session.engine, "Island", seat="B")
        self.assert_closed(session, source)
        self.prepare(session)
        self.upkeep(session, "A")
        self.assertEqual(1, self.trigger_count(session, source))
        self.cast(session, "B", removal, [source.ref])
        self.resolve_top(session)
        self.assertEqual(1, self.trigger_count(session, source))
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[foreign.object_id].zone)
        self.replay(session, load=True)
