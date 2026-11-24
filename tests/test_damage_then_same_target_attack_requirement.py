"""An ordered damage and attack requirement shares one real announced target."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_effect_clause_shapes import FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Bullwhip"
CAPABILITY = FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class DamageThenSameTargetAttackRequirementTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "one-target-damage-attack.sqlite3"
        build_fixture_database([basic, FIXTURES / "damage-then-same-target-attack-requirement-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Damage and resolved attack requirement", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id, event="activate")
        return card, program

    def test_complete_original_keeps_both_ordered_components_and_one_announced_target(self):
        record, program = self.program()
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertEqual(1, program.target_schema["count"])
        self.assertEqual(["creature"], list(program.target_schema["types_any"]))
        first, second = program.effects
        self.assertEqual(("damage", "grant_declaration_restriction_until_end_of_turn"), (first["op"], second["op"]))
        self.assertEqual(("$target.0", "$target.0"), (first["target"], second["card"]))
        self.assertEqual(1, first["amount"])
        for text in (
            record.oracle_text.replace("That creature", "Target creature"),
            record.oracle_text.replace("attacks this turn", "attacks next turn"),
            record.oracle_text.replace("to target creature", "to any target"),
        ):
            compiled = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                           capability_profile=PROFILE)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies for f in compiled.faces for node in f.nodes))

    def test_raw_shared_target_and_parent_sequence_requirement_cannot_be_forged(self):
        _, program = self.program()
        first, second = program.effects
        for changed in (
            replace(program, effects=(second, first)),
            replace(program, effects=(first, {**second, "card": "$target.1"})),
            replace(program, target_schema={**program.target_schema, "count": 2}),
            replace(program, provenance={**program.provenance, "template_id": "fixed-effect-clause-sequence-v1"}),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        capabilities = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=capabilities,
                          capability_closure=self.registry.closure(capabilities, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def scene(self, seed, *, response=None):
        game = self.session(seed)
        engine = game.engine
        whip = self.add(engine, NAME)
        subject = self.add(engine, "Grizzly Bears", seat="B", ref="bullwhip-bound-target")
        other = self.add(engine, "Grizzly Bears", seat="B", ref="bullwhip-other-creature")
        for body in (subject, other):
            body.acquired_control_turn_count = -1
        card = self.add(engine, response, seat="B", zone="hand") if response else None
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="bullwhip-hidden")
        self.assert_closed(game, whip)
        if card:
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="B", mana={"C": 2})
        if response == "Mending Hands":
            game.state.players["B"].mana_pool["W"] = 1
        if response == "Boomerang":
            game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)

        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [entry for entry in options if entry["id"].startswith(f"activate:{whip.ref}:")]
        command = {"action_id": offer["id"], "targets": [subject.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[whip.object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        if card:
            self.cast_for(game, "B", card, [subject.ref])
            self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        return game, subject, other, private

    def native_requirement(self, *, prevented):
        game, subject, other, private = self.scene(508010891 + prevented, response="Mending Hands" if prevented else None)
        self.assertEqual(0 if prevented else 1, game.state.cards[subject.object_id].marked_damage)
        self.assertEqual(0, game.state.cards[other.object_id].marked_damage)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        before = authoritative_state_hash(game.state)
        omitted = game.act("pilot:B", {"action_id": "attack", "attackers": {other.ref: "C"}})
        self.assertFalse(omitted.ok, "A prevented hit still leaves the attack requirement")
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", {"action_id": "attack", "attackers": {subject.ref: "C"}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("C", game.state.combat.attackers[subject.object_id])
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_damage_and_fully_prevented_damage_each_leave_the_original_attack_requirement(self):
        for prevented in (False, True):
            with self.subTest(prevented=prevented):
                self.native_requirement(prevented=prevented)

    def test_actual_departed_sole_target_loses_both_results_without_refunding_the_activation(self):
        game, subject, other, _private = self.scene(508010893, response="Boomerang")
        self.assertEqual("hand", game.state.cards[subject.object_id].zone)
        self.assertEqual(0, game.state.cards[subject.object_id].marked_damage)
        self.assertEqual(0, game.state.cards[other.object_id].marked_damage)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {}})
        self.assertTrue(result.ok, result.summary)
        self.replay(game, load=True)

    def test_actual_prevention_witness_kills_conditioning_the_requirement_on_dealt_damage(self):
        from quorune.effect_runtime.declaration_effects import HANDLERS

        operation = "grant_declaration_restriction_until_end_of_turn"
        original = HANDLERS[operation]

        def conditional(host, effect, *args, **kwargs):
            recipient = host._resolve_object(kwargs["actor"], effect["card"], zones={"battlefield"})
            if recipient.marked_damage == 0:
                return []
            return original(host, effect, *args, **kwargs)

        with patch.dict(HANDLERS, {operation: conditional}):
            with self.assertRaisesRegex(AssertionError, "prevented hit still leaves the attack requirement"):
                self.native_requirement(prevented=True)
