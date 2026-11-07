"""Printed spell self-shuffle uses the successful stack completion."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.spell_self_exile import SELF_SHUFFLE
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record


class TerminalSpellShuffleCompilerTests(unittest.TestCase):
    def test_terminal_shuffle_closes_only_its_own_final_instruction_and_body(self):
        caps = load_default_capability_registry()
        text = "Draw a card. Shuffle Generic Bound Compiler Fixture into its owner's library."
        ir = compile_oracle_card(record(text, "Instant"), capability_registry=caps)
        self.assertEqual("exact", ir.status)
        node, = (node for face in ir.faces for node in face.nodes if node.effects)
        self.assertEqual(text, text[node.span.start:node.span.end])
        self.assertEqual(["draw"], [effect["op"] for effect in node.effects])
        self.assertIn(SELF_SHUFFLE.mechanic, node.mechanics)
        self.assertIn(SELF_SHUFFLE.capability, node.capability_dependencies)
        for invalid in (
            text.replace("Generic Bound Compiler Fixture", "another card"),
            text.replace("its owner's library", "your library"),
            text.replace("Draw a card.", "Perform an unsupported operation."),
            text + " Draw a card.",
        ):
            with self.subTest(invalid=invalid):
                self.assertNotEqual("exact", compile_oracle_card(record(invalid, "Instant"), capability_registry=caps).status)

    def test_omitting_the_terminal_shuffle_compiler_is_detected(self):
        with patch("quorune.compiler.spell_self_exile.terminal_self_shuffle_body", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_terminal_shuffle_closes_only_its_own_final_instruction_and_body()


class TerminalSpellShuffleRuntimeTests(actions.InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "terminal-spell-shuffle.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/fixed-stack-spell-copy-cards.json",
            ROOT / "tests/fixtures/miracle-cards.json",
            ROOT / "tests/fixtures/state-based-destination-cards.json",
            ROOT / "tests/fixtures/terminal-spell-shuffle-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Terminal shuffle", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def prepare(self, session, **kwargs):
        session.state.config.semantic_policy = "trusted_only"
        super().prepare(session, **kwargs)

    def counts(self, session):
        return {seat: session.state.players[seat].stats.get("shuffle_count", 0) for seat in "ABCD"}

    def assert_current_shuffle_program(self, session, card):
        self.assert_closed(session, card)
        program, = (program for program in session.engine.semantics.programs_for_oracle(card.oracle_id)
                    if SELF_SHUFFLE.mechanic in program.coverage)
        self.assertEqual("library", program.destination)
        required = [value for value in program.capability_dependencies if value != SELF_SHUFFLE.capability]
        stripped = replace(program, capability_dependencies=required,
                           capability_closure=self.registry.closure(required, profile="commander_review").to_dict())
        invalid = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(invalid["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{SELF_SHUFFLE.capability}", invalid["blockers"])
        for malformed in (replace(program, destination="graveyard"),
                          replace(program, coverage=[value for value in program.coverage if value != SELF_SHUFFLE.mechanic])):
            self.assertFalse(bind_semantic_program_runtime(
                malformed, capability_registry=self.registry, profile="commander_review",
            )["strict"])

    def test_beacon_and_opponents_copy_shuffle_separate_owner_libraries(self):
        session = self.session(7012401)
        beacon = self.add(session.engine, "Beacon of Destruction", zone="hand")
        copier = self.add(session.engine, "Reverberate", seat="B", zone="hand")
        self.assert_current_shuffle_program(session, beacon)
        self.prepare(session, mana={"C": 3, "R": 2})
        session.state.players["B"].mana_pool["R"] = 2
        counts = self.counts(session)
        life = {seat: session.state.players[seat].life for seat in "CD"}
        self.checkpoint(session)

        self.cast(session, "A", beacon, ["C"])
        original = session.state.stack[-1]
        self.cast(session, "B", copier, [original.ref])
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(counts, self.counts(session))
        command = {"action_id": "choose", "targets": ["D"]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        copied = session.state.stack[-1]
        self.assertEqual("spell_copy", copied.kind)
        self.assertEqual("B", session.state.cards[copied.card_object_id].owner)
        self.resolve_top(session)
        self.assertEqual({**counts, "B": counts["B"] + 1}, self.counts(session))
        self.assertNotIn(copied.card_object_id, session.state.players["B"].zones["library"])
        self.assertEqual("stack", session.state.cards[beacon.object_id].zone)
        self.assertEqual(life["D"] - 5, session.state.players["D"].life)
        self.assertEqual(life["C"], session.state.players["C"].life)
        self.settle(session)
        self.assertEqual({**counts, "A": counts["A"] + 1, "B": counts["B"] + 1}, self.counts(session))
        self.assertEqual(life["C"] - 5, session.state.players["C"].life)
        self.assertEqual("library", session.state.cards[beacon.object_id].zone)
        self.replay(session, load=True)

    def test_zenith_shuffles_once_after_foreign_draw_choice_resumes(self):
        session = self.session(7012402)
        zenith = self.add(session.engine, "Blue Sun's Zenith", zone="hand")
        miracle = self.add(session.engine, "Thunderous Wrath", seat="B", zone="library", ref="zenith-private-draw")
        self.assert_current_shuffle_program(session, zenith)
        self.assert_closed(session, miracle)
        self.prepare(session, mana={"C": 2, "U": 3})
        counts = self.counts(session)
        hand = len(session.state.players["B"].zones["hand"])
        self.checkpoint(session)
        action = self.action(session, "A", zenith)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "x": 2, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("draw.miracle_reveal", self.resolve(session).kind)
        self.assertEqual(["B"], session.state.pending_decision.actors)
        self.assertEqual(counts, self.counts(session))
        self.assertEqual("stack", session.state.cards[zenith.object_id].zone)
        for viewer in "ACD":
            self.assertNotIn(miracle.ref, json.dumps(session.packet("pilot:" + viewer, full=True)))
        self.assertIn(miracle.ref, json.dumps(session.packet("pilot:B", full=True)))
        session = self.replay(session, load=True)
        result = session.act("pilot:B", {"action_id": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.settle(session)
        self.assertEqual(hand + 2, len(session.state.players["B"].zones["hand"]))
        self.assertEqual({**counts, "A": counts["A"] + 1}, self.counts(session))
        self.assertEqual("library", session.state.cards[zenith.object_id].zone)
        self.replay(session, load=True)

    def test_countered_and_all_illegal_beacons_skip_the_shuffle(self):
        session = self.session(7012403)
        first = self.add(session.engine, "Beacon of Destruction", zone="hand", ref="countered-beacon")
        second = self.add(session.engine, "Beacon of Destruction", zone="hand", ref="fizzled-beacon")
        target = self.add(session.engine, "Grizzly Bears", seat="C")
        counter = self.add(session.engine, "Counterspell", seat="B", zone="hand")
        bolt = self.add(session.engine, "Lightning Bolt", seat="B", zone="hand")
        self.prepare(session, mana={"C": 6, "R": 4})
        session.state.players["B"].mana_pool.update({"U": 2, "R": 1})
        counts = self.counts(session)
        self.checkpoint(session)

        self.cast(session, "A", first, [target.ref])
        self.cast(session, "B", counter, [session.state.stack[-1].ref])
        self.settle(session)
        self.assertEqual("graveyard", session.state.cards[first.object_id].zone)
        self.assertEqual(("battlefield", 0), (session.state.cards[target.object_id].zone,
                                             session.state.cards[target.object_id].marked_damage))
        self.cast(session, "A", second, [target.ref])
        self.cast(session, "B", bolt, [target.ref])
        self.settle(session)
        self.assertEqual("graveyard", session.state.cards[second.object_id].zone)
        self.assertEqual(counts, self.counts(session))
        self.replay(session, load=True)
