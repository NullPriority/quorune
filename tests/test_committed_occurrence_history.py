"""Committed occurrences feed the existing public condition consumers."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.public_state_queries import fixed_public_state_condition
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import TurnHistory, TurnHistoryEvent
from quorune.oracle_ir import compile_oracle_card
from quorune.public_history_query_model import (
    COMMITTED_OCCURRENCE_HISTORY_CAPABILITY, PublicHistoryQuerySpec, public_history_query_value,
)
from quorune.record import authoritative_state_hash
from quorune.record_state_provenance import format_state_versions, validate_state_versions
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.casting.public_conditions import prospective_cost_condition_view
from quorune.turn_history import committed_token_count, prospective_cast_history, roll_turn_history
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_public_history_queries as history_controls


_VOID = "a nonland permanent left the battlefield this turn or a spell was warped this turn"
_WARP = PublicHistoryQuerySpec("spell_warped", "any_player")
_LEFT = PublicHistoryQuerySpec("card_left_graveyard")
_MODE = "committed_occurrence_history_version"


class CommittedOccurrenceCompilerTests(unittest.TestCase):
    def test_void_resolution_declares_both_history_arms_and_keeps_one_target(self):
        registry = load_default_capability_registry()
        text = f"History witness deals 2 damage to any target.\nVoid — History witness deals 3 damage instead if {_VOID}."
        ir = compile_oracle_card(replace(record(text, "Sorcery"), name="History witness"), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(1, node.target_schema["count"])
        self.assertIn(COMMITTED_OCCURRENCE_HISTORY_CAPABILITY, node.capability_dependencies)
        query = fixed_public_state_condition(_VOID, source_name="History witness")
        self.assertEqual(("permanent_left", "spell_warped"), tuple(leaf.history_query.event for leaf in query.terms))
        self.assertEqual(("land",), query.terms[0].history_query.excluded_types)
        self.assertEqual({"any_player"}, {leaf.history_query.relation for leaf in query.terms})

    def test_committed_events_require_the_mode_and_exact_closed_payload(self):
        event = TurnHistoryEvent("spell_warped", actor="B", object_incarnation="warped@1")
        history = TurnHistory(turn_sequence=6, events=[event], committed_occurrence_history_version=1)
        self.assertEqual(history, TurnHistory.from_dict(history.to_dict()))
        state = SimpleNamespace(
            turn_sequence=6, turn_history=history,
            players={seat: SimpleNamespace(in_game=True) for seat in "AB"},
        )
        self.assertEqual(1, public_history_query_value(state, "A", _WARP))
        for extra in ({"actor": None}, {"object_incarnation": ""}, {"types": ("land",)}, {"amount": True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                TurnHistoryEvent.from_dict({**event.to_dict(), **extra})
        for mode in (True, 2, None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                TurnHistory.from_dict({**history.to_dict(), _MODE: mode})
        with self.assertRaises(ValueError):
            PublicHistoryQuerySpec("card_left_graveyard", types_all=("creature",))
        state.turn_history = roll_turn_history(history, next_turn_sequence=7, previous_active_player="A")
        state.turn_sequence = 7
        self.assertEqual(1, state.turn_history.committed_occurrence_history_version)
        self.assertEqual(0, public_history_query_value(state, "A", _WARP))
        self.assertEqual({}, state.turn_history.previous_spell_cast_counts)
        state.turn_history = TurnHistory(turn_sequence=7)
        self.assertIsNone(public_history_query_value(state, "A", _WARP))
        self.assertNotIn(_MODE, state.turn_history.to_dict())

    def test_prospective_departure_owner_and_origin_mutation_is_killed(self):
        source = SimpleNamespace(owner="B", zone="graveyard", logical_object_id="borrowed@4", is_card_object=True)
        history = TurnHistory(turn_sequence=6, committed_occurrence_history_version=1)
        state = SimpleNamespace(turn_sequence=6, turn_history=history)
        snapshot = history.to_dict()

        def witness():
            source.zone = "graveyard"
            event, = prospective_cast_history(state, source).events
            self.assertEqual(("card_left_graveyard", "B", "borrowed@4"), (event.kind, event.actor, event.object_incarnation))
            self.assertEqual(snapshot, history.to_dict())
            source.zone = "hand"
            self.assertEqual([], prospective_cast_history(state, source).events)

        witness()
        event = TurnHistoryEvent("card_left_graveyard", actor="B", object_incarnation="borrowed@4")
        with patch("quorune.turn_history._graveyard_departure_fact", return_value=event):
            with self.assertRaises(AssertionError):
                witness()


class CommittedOccurrenceNativeTests(InheritedNativeFixture, unittest.TestCase):
    end_step = history_controls.PublicHistoryQueryNativeTests.end_step

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "committed-occurrences.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "public-history-query-cards", "public-exile-followup-cards",
            "committed-occurrence-history-cards",
        )
        build_fixture_database([ROOT / "tests/fixtures" / f"{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed occurrences", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _actions(self, session, seat="A"):
        return session.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def _cast_alternative(self, session, card, option, *, reserve=None):
        self.priority(session, "A")
        row = next(row for row in self._actions(session) if row.get("card") == card.ref)
        self.assertIn(option, [value["id"] for value in row["cost_options"]])
        command = {"action_id": row["id"], "cost_option": option, "pay": "auto"}
        if reserve:
            command["reserve"] = reserve
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_countered_warp_and_ordinary_cast_are_distinct_for_void(self):
        for index, warped in enumerate((False, True)):
            with self.subTest(warped=warped):
                session = self.session(603_695_101 + index)
                engine = session.engine
                colossus = self.add(engine, "Bygone Colossus", zone="hand")
                watcher = self.add(engine, "Kavaron Skywarden")
                bolt = self.add(engine, "Plasma Bolt", zone="hand")
                counter = self.add(engine, "Counterspell", seat="B", zone="hand")
                for card in (colossus, watcher, bolt, counter):
                    self.assert_closed(session, card)
                self.prepare(session, mana={"C": 9, "R": 1})
                session.state.players["B"].mana_pool["U"] = 2
                self.checkpoint(session)
                if warped:
                    self._cast_alternative(session, colossus, "warp", reserve={"R": 1})
                else:
                    self.cast_for(session, "A", colossus, reserve={"R": 1})
                cast = next(item for item in session.state.stack if item.card_object_id == colossus.object_id)
                self.cast_for(session, "B", counter, [cast.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual("graveyard", session.state.cards[colossus.object_id].zone)
                self.assertEqual(int(warped), public_history_query_value(session.state, "A", _WARP))
                self.priority(session, "A")
                before = session.state.players["C"].life
                self.cast(session, "A", bolt, ["C"])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(before - (3 if warped else 2), session.state.players["C"].life)
                self.end_step(session)
                self.assertEqual(warped, any(item.source_object_id == watcher.object_id for item in session.state.stack))
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(int(warped), session.state.cards[watcher.object_id].counters.get("+1/+1", 0))
                self.replay(session)

    def test_actual_void_discount_counts_another_players_nonland_departure(self):
        for index, land in enumerate((True, False)):
            with self.subTest(land=land):
                session = self.session(603_695_111 + index)
                engine = session.engine
                source = self.add(engine, "Temporal Intervention", zone="hand")
                bounce = self.add(engine, "Boomerang", seat="B", zone="hand")
                permanent = self.add(engine, "Forest" if land else "Grizzly Bears", seat="C")
                discard = self.add(engine, "Generic Bound Growth", seat="C", zone="hand", ref="void-selected")
                private = self.add(engine, "Generic Bound Growth", seat="D", zone="hand", ref="void-unaffected")
                self.assert_closed(session, source)
                self.prepare(session, mana={"B": 1})
                session.state.players["B"].mana_pool["U"] = 2
                self.checkpoint(session)
                self.cast_for(session, "B", bounce, [permanent.ref])
                self.assertIsNone(self.resolve(session))
                self.assertEqual("hand", session.state.cards[permanent.object_id].zone)
                self.priority(session, "A")
                rows = [row for row in self._actions(session) if row.get("card") == source.ref]
                self.assertEqual(not land, bool(rows))
                if land:
                    self.replay(session)
                    continue
                self.cast(session, "A", source, ["C"])
                pending = self.resolve(session)
                self.assertEqual("semantic.choice", pending.kind)
                self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))
                before = authoritative_state_hash(session.state)
                choice = {"action_id": "choose", "card": discard.ref}
                self.assertFalse(session.act("pilot:D", choice).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                result = session.act("pilot:A", choice)
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(session))
                self.assertEqual("graveyard", session.state.cards[discard.object_id].zone)
                self.replay(session)

    def test_actual_graveyard_move_and_flashback_bind_different_owners(self):
        session = self.session(603_695_121)
        engine = session.engine
        anchor = self.add(engine, "Essence Anchor")
        relic = self.add(engine, "Relic Retriever", seat="B")
        exile = self.add(engine, "Release to Memory", zone="hand")
        flashback = self.add(engine, "Think Twice", zone="graveyard")
        other = self.add(engine, "Generic Bound Growth", seat="B", zone="graveyard", ref="departed-instant")
        for card in (anchor, relic, exile, flashback):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 5, "W": 1, "U": 1})
        self.checkpoint(session)
        activation_prefix = "activate:" + anchor.ref + ":"
        self.assertFalse(any(row["id"].startswith(activation_prefix) for row in self._actions(session)))
        self.cast_for(session, "A", exile, ["B"], reserve={"C": 2, "U": 1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[other.object_id].zone)
        self.assertEqual(0, public_history_query_value(session.state, "A", _LEFT))
        self.assertEqual(1, public_history_query_value(session.state, "B", _LEFT))
        self.priority(session, "A")
        self.assertFalse(any(row["id"].startswith(activation_prefix) for row in self._actions(session)))
        before = authoritative_state_hash(session.state)
        view, _ = prospective_cost_condition_view(engine, "A", flashback)
        self.assertEqual(1, public_history_query_value(view.state, "A", _LEFT))
        self.assertEqual(before, authoritative_state_hash(session.state))
        self._cast_alternative(session, flashback, "flashback")
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, public_history_query_value(session.state, "A", _LEFT))
        self.assertEqual("exile", session.state.cards[flashback.object_id].zone)
        self.priority(session, "A")
        action = next(row for row in self._actions(session) if row["id"].startswith(activation_prefix))
        command = {"action_id": action["id"], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.end_step(session)
        self.assertTrue(any(item.source_object_id == relic.object_id for item in session.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertCountEqual(
            [("A", "Zombie Druid Token"), ("B", "Treasure Token")],
            [(card.controller, card.printed_name) for card in session.state.cards.values()
             if card.is_token and card.zone == "battlefield"],
        )
        self.replay(session)

    def test_actual_bennie_excludes_a_resolving_copy_and_counts_created_tokens(self):
        for index, copy_spell in enumerate((True, False)):
            with self.subTest(copy_spell=copy_spell):
                session = self.session(603_695_131 + index)
                engine = session.engine
                bennie = self.add(engine, "Bennie Bracks, Zoologist")
                self.assert_closed(session, bennie)
                if copy_spell:
                    reflection = self.add(engine, "Reflections of Littjara", zone="hand")
                    bear = self.add(engine, "Grizzly Bears", zone="hand")
                    self.assert_closed(session, reflection)
                    self.prepare(session, mana={"C": 5, "U": 1, "G": 1})
                    self.checkpoint(session)
                    self.cast_for(session, "A", reflection, reserve={"C": 1, "G": 1})
                    self.assertEqual("replacement.order", self.resolve(session).kind)
                    ctx = session.packet("pilot:A", full=True)["decision"]["ctx"]
                    option = next(row["id"] for row in ctx["options"] if row["id"].endswith(":bear"))
                    result = session.act("pilot:A", {"action_id": "choose", "replacement": option})
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(session))
                    self.cast(session, "A", bear)
                else:
                    alarm = self.add(engine, "Raise the Alarm", zone="hand")
                    self.prepare(session, mana={"C": 1, "W": 1})
                    self.checkpoint(session)
                    self.cast(session, "A", alarm)
                self.assertIsNone(self.resolve(session))
                self.assertEqual(1 if copy_spell else 2, sum(
                    card.is_token and card.zone == "battlefield" for card in session.state.cards.values()
                ))
                self.assertEqual(0 if copy_spell else 2, committed_token_count(session.state, "A"))
                self.end_step(session)
                self.assertEqual(not copy_spell, any(item.source_object_id == bennie.object_id for item in session.state.stack))
                before = len(session.state.players["A"].zones["hand"])
                self.assertIsNone(self.resolve(session))
                self.assertEqual(before + int(not copy_spell), len(session.state.players["A"].zones["hand"]))
                self.replay(session)

    def test_legacy_missing_occurrence_mode_keeps_its_original_replay_shape(self):
        session = self.session(603_695_141)
        flashback = self.add(session.engine, "Think Twice", zone="graveyard")
        self.prepare(session, mana={"C": 2, "U": 1})
        format_value = format_state_versions(session.state)
        self.assertEqual(1, format_value[_MODE])
        with self.assertRaises(ValueError):
            validate_state_versions({"format": {**format_value, _MODE: True}}, session.state)
        session.state.turn_history = replace(session.state.turn_history, committed_occurrence_history_version=None)
        old_format = format_state_versions(session.state)
        self.assertNotIn(_MODE, old_format)
        validate_state_versions({"format": old_format}, session.state)
        self.checkpoint(session)
        self._cast_alternative(session, flashback, "flashback")
        self.assertIsNone(self.resolve(session))
        self.assertIsNone(public_history_query_value(session.state, "A", _LEFT))
        self.assertNotIn(_MODE, session.state.turn_history.to_dict())
        self.replay(session)
