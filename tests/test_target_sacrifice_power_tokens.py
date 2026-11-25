"""CR 608.2h and 701.21a: one sacrifice and its current controller's tokens."""

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
from quorune.rules.target_recipient_results import (
    TARGET_SACRIFICE_POWER_CAPABILITY as CAPABILITY,
    TARGET_SACRIFICE_POWER_FIELD,
    target_sacrifice_power_effect,
)
from quorune.target_controller_reference import target_controller_reference
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Mercy Killing"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class TargetSacrificePowerTokensTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        basic = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        basic["cards"] = [row for row in basic["cards"] if row["name"] == "Generic Bound Plains"]
        basic["rulings"] = []
        base = folder / "base.json"
        base.write_text(json.dumps(basic), encoding="utf-8")
        path = folder / "target-sacrifice-power.sqlite3"
        build_fixture_database([base, FIXTURES / "target-sacrifice-power-token-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Target sacrifice power", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original(self):
        card = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "resolve")
        return card, program

    def test_complete_original_keeps_one_target_and_its_power_bound_controller_result(self):
        card, program = self.original()
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertEqual(["creature"], program.target_schema["types_any"])
        self.assertEqual(1, program.target_schema["count"])
        self.assertNotIn("controller_relation", program.target_schema)
        prefix, token = program.effects
        self.assertEqual(target_sacrifice_power_effect(), prefix)
        self.assertEqual(target_controller_reference(), token["controller"])
        self.assertEqual("power", token["quantity"]["characteristic"])
        self.assertEqual("target_characteristic", token["quantity"]["origin"])
        for text in (
            card.oracle_text.replace("controller sacrifices", "owner sacrifices"),
            card.oracle_text.replace("creature's power", "creature's toughness"),
            card.oracle_text.replace("Target creature's", "Target permanent's"),
            card.oracle_text + " Draw a card.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                         capability_profile=PROFILE)
                self.assertFalse(any(CAPABILITY in n.capability_dependencies for f in ir.faces for n in f.nodes))
        self.assertEqual("exact", compile_oracle_card(
            card, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)

    def test_raw_sacrifice_sequence_requires_exact_power_recipient_and_owner(self):
        _, program = self.original()
        prefix, token = program.effects
        for effects in (
            (token, prefix),
            ({**prefix, TARGET_SACRIFICE_POWER_FIELD: True}, token),
            (prefix, {**token, "controller": "$controller"}),
            (prefix, {**token, "controller": {**token["controller"], "schema_version": True}}),
            (prefix, {**token, "quantity": {**token["quantity"], "characteristic": "toughness"}}),
            (prefix, {**token, "quantity": 2}),
        ):
            with self.subTest(effects=effects):
                result = bind_semantic_program_runtime(replace(program, effects=effects),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        dependencies = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def tokens(self, game, seat):
        return [card for card in game.state.cards.values()
                if not card.is_card_object and card.zone == "battlefield" and card.controller == seat]

    def play_sacrifice(self, *, redirected):
        game = self.session(701210170 + redirected)
        spell = self.add(game.engine, NAME, zone="hand")
        target = self.add(game.engine, "Darksteel Myr", seat="C")
        target.owner = "D"
        control = self.add(game.engine, "Blind with Anger", seat="B", zone="hand")
        growth = self.add(game.engine, "Giant Growth", seat="C", zone="hand")
        self.add(game.engine, "Forest", seat="C")
        private = self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="mercy-private-card")
        if redirected:
            replacement = self.add(game.engine, "Rest in Peace")
            self.assert_closed(game, replacement)
        for card in (spell, control, growth):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "G": 1})
        game.state.players["B"].mana_pool.update({"C": 3, "R": 1})
        self.checkpoint(game)
        action = self.action(game, "A", spell)
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        command = {"action_id": action["id"], "targets": [target.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [target.ref])
        self.cast_for(game, "B", control, [target.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[target.object_id].controller)
        self.cast_for(game, "C", growth, [target.ref])
        self.resolve_top(game)
        self.assertEqual(3, game.engine._numeric_stat(target.object_id, "power"))
        self.assertIn("indestructible", game.engine._combat_keywords(game.state.cards[target.object_id]))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        return game, target, private

    def assert_three_elf_warriors(self, game):
        for card in self.tokens(game, "B"):
            data = game.engine._effective_card_data(card)
            self.assertEqual("B", card.owner)
            self.assertEqual({"G", "W"}, set(data["colors"]))
            self.assertIn("Elf Warrior", data["type_line"])
            self.assertEqual((1, 1), tuple(game.engine._numeric_stat(card.object_id, stat)
                                          for stat in ("power", "toughness")))
        self.assertEqual(3, len(self.tokens(game, "B")))
        for seat in "ACD":
            self.assertEqual([], self.tokens(game, seat))

    def test_actual_mercy_killing_uses_changed_controller_power_and_replacement(self):
        for redirected in (False, True):
            with self.subTest(redirected=redirected):
                game, target, private = self.play_sacrifice(redirected=redirected)
                destination = "exile" if redirected else "graveyard"
                self.assertIn(target.object_id, game.state.players["D"].zones[destination])
                self.assert_three_elf_warriors(game)
                for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
                    self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
                self.replay(game, load=True)

    def test_actual_mercy_killing_negative_power_sacrifices_and_lost_target_does_nothing(self):
        for escaped in (False, True):
            with self.subTest(escaped=escaped):
                game = self.session(701210172 + escaped)
                spell = self.add(game.engine, NAME, zone="hand")
                target = self.add(game.engine, "Wall of Omens", seat="B")
                response = self.add(game.engine, "Unsummon" if escaped else "Tragic Slip", seat="B", zone="hand")
                self.add(game.engine, "Island" if escaped else "Swamp", seat="B")
                self.assert_closed(game, spell)
                self.prepare(game, mana={"C": 2, "W": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", spell, [target.ref])
                self.cast_for(game, "B", response, [target.ref])
                self.resolve_top(game)
                if not escaped:
                    self.assertEqual(-1, game.engine._numeric_stat(target.object_id, "power"))
                self.assertIsNone(self.resolve(game))
                self.assertEqual("hand" if escaped else "graveyard", game.state.cards[target.object_id].zone)
                for seat in "ABCD":
                    self.assertEqual([], self.tokens(game, seat))
                self.replay(game)

    def test_actual_mercy_killing_count_witness_kills_stale_power_mutation(self):
        from quorune import scalar_effect_amounts
        from quorune.scalar_effect_amount_model import ScalarAmountOrigin

        read = scalar_effect_amounts._reference

        def stale(host, item, spec, **kwargs):
            snapshot = read(host, item, spec, **kwargs)
            if spec.origin is ScalarAmountOrigin.TARGET and spec.characteristic == "power":
                return {**snapshot, "power": 0}
            return snapshot

        with patch.object(scalar_effect_amounts, "_reference", side_effect=stale):
            game, _, _ = self.play_sacrifice(redirected=True)
            with self.assertRaises(AssertionError):
                self.assert_three_elf_warriors(game)

    def test_verified_sacrifice_owner_withdrawal_retains_destroy_and_exile_results(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Target sacrifice power authority withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        for name in (NAME, "Beast Within", "Crib Swap"):
            result, _ = register_card(self.db, registry, name)
            self.assertEqual(name != NAME, result["strict_capability_ready"], result["blockers"])
