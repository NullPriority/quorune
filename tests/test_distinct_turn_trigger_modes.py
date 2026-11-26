
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.modal_selection import (
    DISTINCT_TURN_TRIGGER_MODES_CAPABILITY,
    DISTINCT_TURN_TRIGGER_MODES_FIELD,
)
from quorune.turn_history_model import TRIGGER_MODE_CHOSEN_EVENT
from scripts.build_test_database import build_fixture_database


FIXTURE = ROOT / "tests/fixtures/distinct-turn-trigger-mode-cards.json"
MODES = ["mode_1", "mode_2", "mode_3"]
ALLIANCE_NAME = "Gala Greeters"


class DistinctTurnTriggerModeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "distinct-turn-trigger-modes.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Distinct trigger modes",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    def _cast_tokens(self, session, spell, *, count, seat="A"):
        offered = self.action(session, seat, spell)
        result = session.act("pilot:" + seat, {"action_id": offered["id"], "x": count, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        spell_ref = session.state.stack[-1].ref
        self.until(session, lambda game: all(item.ref != spell_ref for item in game.state.stack))

    def _order(self, session, *, count, seat="A"):
        self.assertEqual("trigger.order", session.state.pending_decision.kind)
        self.assertEqual(["pilot:" + seat], session.pending_principals())
        batch, = session.state.pending_trigger_batches
        refs = [item.ref for item in batch.groups[0].items]
        self.assertEqual(count, len(refs))
        result = session.act("pilot:" + seat, {"action_id": "order", "triggers": refs})
        self.assertTrue(result.ok, result.summary)

    def _assert_modes(self, session, expected, *, seat="A"):
        self.assertEqual("semantic.target", session.state.pending_decision.kind)
        self.assertEqual(["pilot:" + seat], session.pending_principals())
        decision = session.packet("pilot:" + seat, full=True)["decision"]
        schema = decision["ctx"]["target_schema"]
        self.assertEqual(expected, schema["legal_modes"])
        self.assertEqual((1, 1), (schema["min_modes"], schema["max_modes"]))
        self.assertEqual([], schema["legal_refs"])
        for other in "ABCD":
            if other != seat:
                self.assertIsNone(session.packet("pilot:" + other, full=True)["decision"])

    def _choose(self, session, mode, *, seat="A"):
        result = session.act("pilot:" + seat, {"action_id": "choose", "modes": [mode], "targets": []})
        self.assertTrue(result.ok, result.summary)

    def _reject(self, session, modes, *, seat="A", targets=()):
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:" + seat, {"action_id": "choose", "modes": modes, "targets": list(targets)})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def _mode_events(self, session):
        return [row for row in session.state.turn_history.events if row.kind == TRIGGER_MODE_CHOSEN_EVENT]

    def test_greeters_simultaneous_creatures_consume_three_modes_during_placement(self):
        session = self.session(60337001)
        source = self.add(session.engine, ALLIANCE_NAME, zone="hand")
        burst = self.add(session.engine, "Secure the Wastes", zone="hand")
        late = self.add(session.engine, "Grizzly Bears", zone="hand")
        next_burst = self.add(session.engine, "Secure the Wastes", zone="hand", ref="greeters-next-turn")
        self.add(session.engine, "Generic Bound Plains")
        self.add(session.engine, "Sol Ring")
        private = self.add(session.engine, "Unsummon", seat="D", zone="hand", ref="greeters-hidden")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 7, "G": 2, "W": 2})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.settle(session)
        self.assertEqual([], self._mode_events(session))
        life = session.state.players["A"].life

        self._cast_tokens(session, burst, count=4)
        self._order(session, count=4)
        self._assert_modes(session, MODES)
        self._reject(session, [])
        self._reject(session, MODES[:2])
        self._reject(session, ["mode_1"], seat="B")
        self._choose(session, "mode_1")

        self._assert_modes(session, MODES[1:])
        self._reject(session, ["mode_1"])
        session = self.replay(session, load=True)
        self._assert_modes(session, MODES[1:])
        self._choose(session, "mode_2")
        self._assert_modes(session, MODES[2:])
        self._choose(session, "mode_3")

        self.assertEqual("priority", session.state.pending_decision.kind)
        self.assertEqual([], session.state.pending_trigger_batches)
        self.assertEqual(3, self.trigger_count(session, source))
        self.assertEqual(
            [("mode_1",), ("mode_2",), ("mode_3",)],
            [tuple(item.modes) for item in session.state.stack if item.source_object_id == source.object_id],
        )
        self.assertEqual([1, 2, 3], [row.amount for row in self._mode_events(session)])
        self.assertEqual({}, session.state.cards[source.object_id].counters)
        self.assertEqual(life, session.state.players["A"].life)
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:A", full=True)))

        self.settle(session)
        self.assertEqual({"+1/+1": 1}, session.state.cards[source.object_id].counters)
        self.assertEqual(life + 2, session.state.players["A"].life)
        treasure, = [card for card in session.state.cards.values() if card.is_token and card.printed_name == "Treasure Token"]
        self.assertTrue(treasure.tapped)
        self.assertEqual("A", treasure.controller)
        self.cast(session, "A", late)
        self.settle(session)
        self.assertEqual([1, 2, 3], [row.amount for row in self._mode_events(session)])
        self.assertEqual(0, self.trigger_count(session, source))

        previous_turn = session.state.turn_sequence
        for _ in range(4):
            self.until(session, lambda game: (
                game.state.turn_sequence > previous_turn or game.state.pending_decision.kind == "cleanup.discard"
            ))
            if session.state.turn_sequence > previous_turn:
                break
            principal, = session.pending_principals()
            context = session.packet(principal, full=True)["decision"]["ctx"]
            selected = [card["id"] for card in context["hand"][:context["count"]]]
            result = session.act(principal, {"action_id": "discard", "cards": selected})
            self.assertTrue(result.ok, result.summary)
        self.assertGreater(session.state.turn_sequence, previous_turn)
        self.assertEqual([], self._mode_events(session))
        self.priority(session, "A")
        self._cast_tokens(session, next_burst, count=1)
        self._assert_modes(session, MODES)
        self._choose(session, "mode_1")
        self.settle(session)
        self.assertEqual({"+1/+1": 2}, session.state.cards[source.object_id].counters)
        self.replay(session, load=True)

    def test_greeters_new_incarnation_has_fresh_modes_and_old_counter_does_not_follow(self):
        session = self.session(60337002)
        source = self.add(session.engine, ALLIANCE_NAME)
        entrant = self.add(session.engine, "Grizzly Bears", zone="hand")
        next_entrant = self.add(session.engine, "Grizzly Bears", zone="hand", ref="greeters-next-creature")
        blink = self.add(session.engine, "Ephemerate", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 2, "G": 2, "W": 1})
        self.checkpoint(session)
        self.cast(session, "A", entrant)
        self.resolve_top(session)
        self._assert_modes(session, MODES)
        self._choose(session, "mode_1")
        previous = session.state.cards[source.object_id].logical_object_id
        self.cast(session, "A", blink, [session.state.cards[source.object_id].ref])
        self.resolve_top(session)
        returned = session.state.cards[source.object_id]
        self.assertNotEqual(previous, returned.logical_object_id)
        self.assertEqual("battlefield", returned.zone)
        self.assertEqual(1, self.trigger_count(session, source))
        session = self.replay(session, load=True)
        self.settle(session)
        self.assertEqual({}, session.state.cards[source.object_id].counters)
        self.assertEqual([previous], [entry.object_incarnation for entry in self._mode_events(session)])

        self.cast(session, "A", next_entrant)
        self.resolve_top(session)
        self._assert_modes(session, MODES)
        self._choose(session, "mode_1")
        self.settle(session)
        returned = session.state.cards[source.object_id]
        self.assertEqual({"+1/+1": 1}, returned.counters)
        self.assertEqual([previous, returned.logical_object_id],
                         [entry.object_incarnation for entry in self._mode_events(session)])
        self.replay(session, load=True)

    def test_greeters_copy_keeps_original_mode_and_countered_mode_remains_spent(self):
        session = self.session(60337003)
        source = self.add(session.engine, ALLIANCE_NAME)
        burst = self.add(session.engine, "Secure the Wastes", zone="hand")
        extra = self.add(session.engine, "Grizzly Bears", zone="hand")
        resonator = self.add(session.engine, "Strionic Resonator")
        disallow = self.add(session.engine, "Disallow", seat="B", zone="hand")
        self.add(session.engine, "Island", seat="B", ref="greeters-island-1")
        self.add(session.engine, "Island", seat="B", ref="greeters-island-2")
        self.add(session.engine, "Sol Ring", seat="B")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 5, "G": 1, "W": 1})
        self.checkpoint(session)
        life = session.state.players["A"].life
        self._cast_tokens(session, burst, count=2)
        self._order(session, count=2)
        self._assert_modes(session, MODES)
        self._choose(session, "mode_3")
        self._assert_modes(session, MODES[:2])
        self._choose(session, "mode_2")
        gain, = [item for item in session.state.stack if item.modes == ["mode_3"]]
        treasure, = [item for item in session.state.stack if item.modes == ["mode_2"]]

        self.priority(session, "A")
        action = next(
            row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            if row["id"].startswith(f"activate:{resonator.ref}:")
        )
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [gain.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        clone = session.state.stack[-1]
        self.assertNotEqual(gain.ref, clone.ref)
        self.assertEqual(["mode_3"], clone.modes)
        self.assertEqual([3, 2], [row.amount for row in self._mode_events(session)])

        self.cast(session, "B", disallow, [treasure.ref])
        self.settle(session)
        self.assertEqual(life + 4, session.state.players["A"].life)
        self.assertFalse([card for card in session.state.cards.values() if card.printed_name == "Treasure Token"])
        self.cast(session, "A", extra)
        self.resolve_top(session)
        self._assert_modes(session, MODES[:1])
        self._choose(session, "mode_1")
        self.settle(session)
        self.assertEqual([3, 2, 1], [row.amount for row in self._mode_events(session)])
        self.replay(session, load=True)

    def test_greeters_exact_shape_and_withdrawn_runtime_owner_fail_closed(self):
        original = self.db.lookup(ALLIANCE_NAME, fuzzy=False)
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("creature.enter", node.event)
        self.assertEqual(1, node.target_schema[DISTINCT_TURN_TRIGGER_MODES_FIELD])
        self.assertIn(DISTINCT_TURN_TRIGGER_MODES_CAPABILITY, node.capability_dependencies)
        for text in (
            original.oracle_text.replace("another creature you control", "a creature you control"),
            original.oracle_text.replace("Create a tapped Treasure token.", "Target player gains 2 life."),
            original.oracle_text.replace("You gain 2 life.", "You invent a new rule."),
        ):
            with self.subTest(text=text):
                changed = compile_oracle_card(replace(original, oracle_text=text),
                                              capability_registry=self.registry, capability_profile="commander_review")
                self.assertNotEqual("exact", changed.status)

        session = self.session(60337004)
        source = self.add(session.engine, ALLIANCE_NAME)
        self.assert_closed(session, source)
        program, = session.engine.semantics.programs_for_oracle(source.oracle_id)
        deps = tuple(dep for dep in program.capability_dependencies if dep != DISTINCT_TURN_TRIGGER_MODES_CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + DISTINCT_TURN_TRIGGER_MODES_CAPABILITY,
                      result["blockers"])


    def test_mutant_without_mode_filter_is_rejected_by_real_placement(self):
        def allow_spent_modes(state, item, schema):
            return schema

        with patch("quorune.rules.modal_selection.bind_distinct_turn_trigger_modes", side_effect=allow_spent_modes):
            with self.assertRaisesRegex(AssertionError, "mode_1"):
                self.test_greeters_simultaneous_creatures_consume_three_modes_during_placement()
