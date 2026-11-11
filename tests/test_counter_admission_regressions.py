"""Whole-card counter admission and original-card replacement consequences."""

from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_compiled_reviewed_trigger_consumers as native


FIXTURE = ROOT / "tests/fixtures/counter-admission-regression-cards.json"


class CounterAdmissionCompilerTests(unittest.TestCase):
    def test_original_cohort_has_normal_whole_card_runtime_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "counter-admission.sqlite3"
            build_fixture_database(FIXTURE, path)
            capabilities = load_default_capability_registry()
            registry = SemanticRegistry()
            with CardDatabase(path) as db:
                for card in db.iter_cards():
                    with self.subTest(card=card.name):
                        ir = compile_oracle_card(card, capability_registry=capabilities,
                            capability_profile="commander_review")
                        self.assertEqual("exact", ir.status, ir.material_residuals)
                        program = compile_best_available_card_program(db, card, semantic_registry=registry,
                            capability_registry=capabilities, capability_profile="commander_review")
                        self.assertEqual("capability_closed", program.trust_closure["trust_basis"])
                        binding = bind_card_program_runtime(program, capability_registry=capabilities,
                            profile="commander_review")
                        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])


class CounterAdmissionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.CompiledReviewedTriggerConsumerTests.session
    add = native.CompiledReviewedTriggerConsumerTests.add
    ready = native.CompiledReviewedTriggerConsumerTests.ready
    prepare = native.CompiledReviewedTriggerConsumerTests.prepare
    checkpoint = native.CompiledReviewedTriggerConsumerTests.checkpoint
    priority = native.CompiledReviewedTriggerConsumerTests.priority
    actions = native.CompiledReviewedTriggerConsumerTests.actions
    binding = native.CompiledReviewedTriggerConsumerTests.binding
    reject = native.CompiledReviewedTriggerConsumerTests.reject
    settle = native.CompiledReviewedTriggerConsumerTests.settle
    replay = native.CompiledReviewedTriggerConsumerTests.replay
    cast = native.CompiledReviewedTriggerConsumerTests.cast
    choose = native.CompiledReviewedTriggerConsumerTests.choose
    advance = native.CompiledReviewedTriggerConsumerTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "counter-admission-native.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/counter-replacement-cards.json",
            FIXTURE,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Counter admission witnesses", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_unleash_real_cast_preserves_entry_choice_quantity_and_block_legality(self):
        for index, apply_counter in enumerate((True, False)):
            with self.subTest(apply_counter=apply_counter):
                session = self.session(974_000 + index)
                source = self.add(session.engine, "Dead Reveler", zone="hand")
                doubler = self.add(session.engine, "Doubling Season")
                attacker = self.add(session.engine, "Generic Bound Body", seat="B")
                self.ready(session, {"C": 5, "B": 1})
                self.assertTrue(self.binding(session, doubler)["strict_capability_ready"])
                self.cast(session, source)
                self.assertEqual("replacement.order", self.settle(session).kind)
                session = self.replay(session, load=True)
                options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                selected = next(row["id"] for row in options if row["id"].startswith("decline:") != apply_counter)
                self.choose(session, choices={"replacement": selected})
                for _ in range(6):
                    decision = self.settle(session)
                    if decision is None:
                        break
                    self.assertEqual("replacement.order", decision.kind)
                    options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                    self.choose(session, choices={"replacement": options[0]["id"]})
                else:
                    self.fail("Entry replacement did not complete")
                self.assertEqual(2 if apply_counter else 0,
                    session.state.cards[source.object_id].counters.get("+1/+1", 0))
                self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
                session = self.replay(session, load=True)
                self.advance(session, lambda s: s.state.active_player == "B"
                    and getattr(s.state.pending_decision, "kind", None) == "combat.attackers")
                command = {"action_id": "attack", "atk": {attacker.ref: "A"}}
                self.reject(session, command, seat="D")
                result = session.act("pilot:B", command)
                self.assertTrue(result.ok, result.summary)
                self.advance(session, lambda s: getattr(s.state.pending_decision, "kind", None) == "combat.blockers")
                blocked = {"action_id": "block", "blocks": {source.ref: attacker.ref}}
                if apply_counter:
                    self.reject(session, blocked, seat="A")
                    blocked = {"action_id": "block", "blocks": {}}
                else:
                    self.reject(session, blocked, seat="D")
                result = session.act("pilot:A", blocked)
                self.assertTrue(result.ok, result.summary)
                self.advance(session, lambda s: s.state.active_player == "B" and s.state.phase == "postcombat_main")
                self.assertEqual(38 if apply_counter else 40, session.state.players["A"].life)
                self.replay(session, load=True)

    def test_real_counter_spells_keep_distinct_reused_and_omitted_target_roles(self):
        for index, mode in enumerate(("distinct", "reused", "omitted")):
            with self.subTest(mode=mode):
                session = self.session(974_010 + index)
                name = "Stand Together" if mode == "distinct" else "River Heralds' Boon"
                source = self.add(session.engine, name, zone="hand")
                own = self.add(session.engine, "Generic Bound Body" if mode == "distinct" else "Merfolk of the Pearl Trident")
                other = self.add(session.engine, "Generic Bound Body", seat="B")
                self.add(session.engine, "Doubling Season")
                self.ready(session, {"C": 5, "G": 2})
                row = next(row for row in self.actions(session) if row.get("card") == source.ref)
                if mode == "distinct":
                    self.reject(session, {"action_id": row["id"], "targets": [own.ref, own.ref], "pay": "auto"})
                targets = [own.ref, other.ref] if mode == "distinct" else [own.ref, own.ref] if mode == "reused" else [own.ref]
                self.cast(session, source, targets)
                session = self.replay(session, load=True)
                self.assertIsNone(self.settle(session))
                self.assertEqual(2 if mode == "omitted" else 4,
                    session.state.cards[own.object_id].counters.get("+1/+1", 0))
                self.assertEqual(2 if mode == "distinct" else 0,
                    session.state.cards[other.object_id].counters.get("+1/+1", 0))
                self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                self.replay(session, load=True)
