"""Verify normal and paid-Kicker modal choices against the actual original spells."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.kicked_modal import KICKED_MODAL_CAPABILITY, KICKED_MODAL_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.greatest_power_life import GREATEST_POWER_LIFE_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ABUNDANCE = "Inscription of Abundance"
RUIN = "Inscription of Ruin"
PROFILE = "commander_review"


class KickedModalTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "kicked-modal.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "nonartifact-nonblack-mass-destruction-cards.json",
            fixtures / "independent-target-clause-cards.json",
            fixtures / "kicked-modal-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Kicked modal choices", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_both_original_modal_envelopes_require_exact_price_and_branch_authority(self):
        for name in (ABUNDANCE, RUIN):
            with self.subTest(name=name):
                record = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                program, = [p for p in semantics.programs_for_oracle(record.oracle_id) if p.event == "resolve"]
                self.assertIn(KICKED_MODAL_CAPABILITY, program.capability_dependencies)
                self.assertEqual((1, 1), (program.target_schema["min_modes"], program.target_schema["max_modes"]))
                self.assertEqual(["mode_1", "mode_2", "mode_3"], program.cost_schema[KICKED_MODAL_FIELD]["mode_ids"])
                if name == ABUNDANCE:
                    self.assertIn(GREATEST_POWER_LIFE_CAPABILITY, program.capability_dependencies)
                    without_life = tuple(c for c in program.capability_dependencies if c != GREATEST_POWER_LIFE_CAPABILITY)
                    incomplete = replace(program, capability_dependencies=without_life,
                        capability_closure=self.registry.closure(without_life, profile=PROFILE).to_dict())
                    self.assertFalse(bind_semantic_program_runtime(
                        incomplete, capability_registry=self.registry, profile=PROFILE,
                    )["strict"])
                requirements = tuple(c for c in program.capability_dependencies if c != KICKED_MODAL_CAPABILITY)
                altered = deepcopy(program.cost_schema)
                altered[KICKED_MODAL_FIELD]["mode_ids"].reverse()
                for forged in (
                    replace(program, cost_schema=altered),
                    replace(program, capability_dependencies=requirements,
                            capability_closure=self.registry.closure(requirements, profile=PROFILE).to_dict()),
                ):
                    self.assertFalse(bind_semantic_program_runtime(
                        forged, capability_registry=self.registry, profile=PROFILE,
                    )["strict"])

    def accept(self, game, seat, command):
        outcome = game.act("pilot:" + seat, command)
        self.assertTrue(outcome.ok, outcome.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def cast_command(self, game, card, *, mode, targets, kicked=True):
        row = self.action(game, "A", card)
        return {"action_id": row["id"], "pay": "auto", "cost_option": "kicked" if kicked else "normal",
                "modes": mode, "targets": targets}

    def test_unpaid_and_duplicate_modes_reject_and_zero_modes_still_pay_the_kicker(self):
        game = self.session(9455101)
        spell = self.add(game.engine, ABUNDANCE, zone="hand")
        creature = self.add(game.engine, "Grizzly Bears")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 3, "G": 2})
        self.checkpoint(game)
        self.reject(game, "A", self.cast_command(game, spell, mode=["mode_1", "mode_2"],
                                                targets=[creature.ref, "A"], kicked=False))
        self.reject(game, "A", self.cast_command(game, spell, mode=["mode_1", "mode_1"],
                                                targets=[creature.ref, creature.ref]))
        self.accept(game, "A", self.cast_command(game, spell, mode=[], targets=[]))
        self.assertEqual([], game.state.stack[-1].modes)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual({}, game.state.cards[creature.object_id].counters)
        self.replay(game, load=True)

    def prove_abundance_copy(self):
        game = self.session(9455111)
        spell = self.add(game.engine, ABUNDANCE, zone="hand")
        echo = self.add(game.engine, "Reverberate", seat="B", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", ref="abundance-own-creature")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="abundance-fight-opponent")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 3, "G": 2})
        game.state.players["B"].mana_pool["R"] = 2
        self.checkpoint(game)
        modes = ["mode_1", "mode_2", "mode_3"]
        targets = [creature.ref, "A", creature.ref, other.ref]
        command = self.cast_command(game, spell, mode=list(reversed(modes)), targets=targets)
        self.reject(game, "D", command)
        self.accept(game, "A", command)
        original = game.state.stack[-1]
        self.assertEqual(modes, original.modes)
        self.cast_for(game, "B", echo, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, list(pending.actors)))
        game = self.replay(game, load=True)
        retargeted = [creature.ref, "A", other.ref, creature.ref]
        command = {"action_id": "choose", "targets": retargeted}
        self.reject(game, "D", command)
        current_snapshot = game.engine._target_snapshot

        def different_incarnation(ref):
            snapshot = current_snapshot(ref)
            if ref == creature.ref:
                snapshot["zone_change_counter"] += 1
            return snapshot

        with patch.object(game.engine, "_target_snapshot", side_effect=different_incarnation):
            self.reject(game, "B", command)
        self.accept(game, "B", command)
        copy = game.state.stack[-1]
        self.assertEqual(("spell_copy", modes), (copy.kind, copy.modes))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(44, game.state.players["A"].life)
        self.assertEqual({"+1/+1": 2}, game.state.cards[creature.object_id].counters)
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.assertEqual(2, game.state.cards[creature.object_id].marked_damage)
        self.resolve_top(game)
        self.assertEqual(50, game.state.players["A"].life)
        self.assertEqual({"+1/+1": 4}, game.state.cards[creature.object_id].counters)
        self.assertEqual(2, game.state.cards[creature.object_id].marked_damage)
        self.replay(game, load=True)

    def test_kicked_copy_uses_printed_order_and_legal_modes_still_resolve_after_fight_target_loss(self):
        self.prove_abundance_copy()

    def test_normal_life_mode_floors_negative_creature_power_at_zero(self):
        game = self.session(9455121)
        spell = self.add(game.engine, ABUNDANCE, zone="hand")
        poor = self.add(game.engine, "Ornithopter", seat="B", ref="abundance-negative-power")
        poor.counters["-1/-1"] = 1
        private = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand", ref="abundance-hidden-creature")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "G": 1})
        self.checkpoint(game)
        self.assertEqual(-1, game.engine._numeric_stat(poor.object_id, "power"))
        self.accept(game, "A", self.cast_command(game, spell, mode=["mode_2"], targets=["B"], kicked=False))
        self.resolve_top(game)
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual("battlefield", game.state.cards[poor.object_id].zone)
        for viewer in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_kicked_ruin_holds_private_affected_player_discard_before_return_and_destroy(self):
        game = self.session(9455131)
        spell = self.add(game.engine, RUIN, zone="hand")
        buried = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="ruin-original-graveyard-target")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="ruin-original-battlefield-target")
        secrets = [game.state.cards[oid].ref for oid in game.state.players["B"].zones["hand"][:2]]
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 4, "B": 3})
        self.checkpoint(game)
        modes = ["mode_1", "mode_2", "mode_3"]
        self.reject(game, "A", self.cast_command(game, spell, mode=modes, targets=["B", buried.ref, buried.ref]))
        self.accept(game, "A", self.cast_command(game, spell, mode=modes, targets=["B", buried.ref, other.ref]))
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, list(pending.actors)))
        for seat in "ACD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        game = self.replay(game, load=True)
        self.reject(game, "D", {"action_id": "choose", "cards": secrets})
        self.accept(game, "B", {"action_id": "choose", "cards": secrets})
        self.assertEqual("battlefield", game.state.cards[buried.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.replay(game, load=True)

    def test_mutation_that_ignores_paid_kicker_fails_the_complete_copied_cast(self):
        def retain_base_schema(_self, schema):
            return schema

        with patch("quorune.kicked_modal.KickedModalSpec.kicked_schema", retain_base_schema):
            with self.assertRaises(AssertionError):
                self.prove_abundance_copy()

    def test_mutation_that_gains_life_at_negative_power_is_detected(self):
        with patch("quorune.rules.greatest_power_life.resolve_greatest_power_life_value", return_value=1):
            with self.assertRaises(AssertionError):
                self.test_normal_life_mode_floors_negative_creature_power_at_zero()
