"""Thunderstaff's live untapped prevention uses the normal per-event damage owner."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.damage_replacements import (
    FixedDamagePreventionHandler,
    FixedUntappedDamagePreventionHandler,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Thunderstaff"
HANDLER = "prevention.damage.fixed.untapped.v3"
CAPABILITY = "damage.prevention.static_fixed"
PROFILE = "commander_review"


class UntappedSourceFixedCombatPreventionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "untapped-static-prevention.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures/untapped-source-fixed-combat-prevention-cards.json"], db_path,
        )
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Untapped source prevention", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        record = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(record.oracle_id)
        program, = [entry for entry in programs if entry.event == "damage"]
        self.assertEqual(1, sum(entry.event == "activate" for entry in programs))
        return record, program

    def test_complete_original_retains_its_paid_sibling_and_versioned_live_condition(self):
        record, program = self.program()
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, program.coverage)
        handler, = program.handlers
        self.assertEqual(HANDLER, handler["handler_id"])
        self.assertEqual(3, handler["schema_version"])
        self.assertEqual({"amount": 1}, handler["modification"])
        self.assertEqual({
            "source_controller_relation": "any", "target_controller_relation": "source_controller",
            "target_kinds": ["player"], "source_types_all": ["creature"], "target_types_all": [],
            "combat": True,
        }, thaw_value(handler["condition"]))
        for bad in (
            record.oracle_text.replace("this artifact is untapped", "this artifact is tapped"),
            record.oracle_text.replace("a creature would deal", "a source would deal"),
            record.oracle_text.replace("combat damage to you", "damage to you"),
            record.oracle_text.replace("combat damage to you", "combat damage to an opponent"),
        ):
            ir = compile_oracle_card(replace(record, oracle_text=bad), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, bad)
            self.assertTrue(any(node.event == "activate" for face in ir.faces for node in face.nodes))

    def test_raw_untapped_prevention_rejects_envelope_scope_and_missing_owner(self):
        _, program = self.program()
        for field, value in (("combat", False), ("source_types_all", []), ("target_kinds", ["permanent"])):
            raw = deepcopy(program.handlers[0])
            raw["condition"][field] = value
            with self.assertRaisesRegex(SemanticNodeError, "requires creature combat damage"):
                replace(program, handlers=(raw,))
        raw = deepcopy(program.handlers[0])
        raw["schema_version"] = 2
        with self.assertRaises(SemanticNodeError):
            replace(program, handlers=(raw,))
        capabilities = [value for value in program.capability_dependencies if value != CAPABILITY]
        closure = self.registry.closure(capabilities, profile=PROFILE).to_dict() if capabilities else None
        missing = replace(program, capability_dependencies=capabilities, capability_closure=closure)
        binding = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"], binding)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])

    def scene(self, seed):
        game = self.session(seed)
        engine = game.engine
        staff = self.add(engine, NAME)
        key = self.add(engine, "Voltaic Key")
        ace = self.add(engine, "Fencing Ace", seat="B")
        bears = [self.add(engine, "Grizzly Bears", seat="B", ref=f"thunderstaff-attacker-{i}") for i in range(3)]
        for attacker in (ace, *bears):
            attacker.acquired_control_turn_count = -1
        for seat in ("A", "C"):
            self.add(engine, "Grizzly Bears", seat=seat, ref=f"thunderstaff-blocker-{seat}")
        bolt = self.add(engine, "Lightning Bolt", seat="B", zone="hand")
        self.add(engine, "Mountain", seat="B")
        for index in range(3):
            self.add(engine, "Plains", ref=f"thunderstaff-payment-{index}")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="thunderstaff-hidden")
        for card in (staff, key, ace, bolt):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B")
        self.checkpoint(game)
        return game, staff, key, (ace, *bears), bolt, private

    def combat(self, game, attackers):
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        command = {"action_id": "attack", "attackers": {
            card.ref: defender for card, defender in zip(attackers, ("A", "A", "A", "C"))
        }}
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        defenders = []
        for _ in range(2):
            self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
            principal, = game.pending_principals()
            defenders.append(principal)
            result = game.act(principal, {"action_id": "block", "blocks": {}})
            self.assertTrue(result.ok, result.summary)
        self.assertCountEqual(("pilot:A", "pilot:C"), defenders)

    def activate(self, game, source, targets=()):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        command = {"action_id": offer["id"], "targets": list(targets), "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.resolve_top(game)

    def damage_boundary(self, game, step_index):
        self.until(game, lambda current: current.state.step == "combat_damage"
                   and current.state.combat.damage_step_index == step_index
                   and current.state.pending_decision.kind == "priority")

    def test_actual_untapped_source_prevents_each_creature_event_in_both_strike_steps(self):
        game, staff, _key, attackers, bolt, private = self.scene(61510621)
        self.cast_for(game, "B", bolt, ["A"])
        self.resolve_top(game)
        self.assertEqual(37, game.state.players["A"].life)
        self.combat(game, attackers)
        self.damage_boundary(game, 0)
        self.assertEqual(37, game.state.players["A"].life)
        game = self.replay(game, load=True)
        self.damage_boundary(game, 1)
        self.assertFalse(game.state.cards[staff.object_id].tapped)
        self.assertEqual(35, game.state.players["A"].life)
        self.assertEqual(38, game.state.players["C"].life)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.assertIn(private.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.replay(game, load=True)

    def test_actual_tap_cost_and_untap_change_only_future_damage_batches(self):
        game, staff, key, attackers, _bolt, _private = self.scene(61510622)
        self.combat(game, attackers)
        self.activate(game, staff)
        for body, power in zip(attackers, (2, 3, 3, 3)):
            self.assertEqual(power, game.engine._numeric_stat(body.object_id, "power"))
        self.damage_boundary(game, 0)
        self.assertEqual(38, game.state.players["A"].life, "Tap cost disables first-strike prevention")
        game = self.replay(game, load=True)
        self.activate(game, key, [staff.ref])
        self.assertFalse(game.state.cards[staff.object_id].tapped)
        self.damage_boundary(game, 1)
        self.assertEqual(33, game.state.players["A"].life)
        self.assertEqual(37, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_actual_tapped_combat_witness_kills_ignoring_current_source_state(self):
        def ignore_tapped(owner, descriptor, context):
            return (FixedDamagePreventionHandler.replacement_effect(owner, descriptor, context),)

        with patch.object(FixedUntappedDamagePreventionHandler, "lower", ignore_tapped):
            with self.assertRaisesRegex(AssertionError, "Tap cost disables first-strike prevention"):
                self.test_actual_tap_cost_and_untap_change_only_future_damage_batches()
