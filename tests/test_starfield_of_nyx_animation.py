"""Live threshold, earlier-layer receiver membership, and base stats for enchantments."""

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
from quorune.continuous_effect_model import (
    ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin, ContinuousObjectIdentity, ContinuousOperation, Layer,
)
from quorune.continuous_effects import CharacteristicState
from quorune.continuous_type_dependencies import battlefield_type_effect_order
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.object_predicate import ObjectQuerySpec
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.fixed_characteristic_settings import (
    CONTROLLED_ENCHANTMENT_ANIMATION_CAPABILITY, ControlledEnchantmentAnimationHandler,
)
from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Starfield of Nyx"
PROFILE = "commander_review"


class StarfieldOfNyxAnimationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "starfield-of-nyx.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "starfield-of-nyx-animation-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Starfield of Nyx", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_static_result_requires_original_threshold_receiver_and_base_setting(self):
        card = self.db.lookup(NAME, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        static, = (program for program in semantics.programs_for_oracle(card.oracle_id)
                   if CONTROLLED_ENCHANTMENT_ANIMATION_CAPABILITY in program.capability_dependencies)
        handler = ControlledEnchantmentAnimationHandler()
        descriptor, = static.handlers
        handler.validate(descriptor)
        changed = {**descriptor, "source_condition": {**descriptor["source_condition"], "amount": 4}}
        with self.assertRaisesRegex(SemanticNodeError, "original public threshold"):
            replace(static, handlers=[changed])
        dependencies = tuple(value for value in static.capability_dependencies
                             if value != CONTROLLED_ENCHANTMENT_ANIMATION_CAPABILITY)
        altered = replace(static, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(
            altered, capability_registry=self.registry, profile=PROFILE,
        )["strict"])
        for before, after in (("five or more", "four or more"), ("each other non-Aura", "each non-Aura"),
                              ("its mana value", "three"), ("enchantment you control", "enchantment")):
            with self.subTest(after=after):
                copy = replace(card, oracle_text=card.oracle_text.replace(before, after))
                ir = compile_oracle_card(copy, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

    def assert_types(self, game, obj, expected):
        data = game.engine._effective_card_data(game.state.cards[obj.object_id])
        self.assertEqual(expected, game.engine._type_parts(data["type_line"])[0])

    def test_actual_threshold_counts_auras_and_animates_only_other_controlled_non_auras_with_their_mana_values(self):
        game = self.session(47839701)
        source = self.add(game.engine, NAME)
        presence = self.add(game.engine, "Enchantress's Presence", ref="controlled-presence")
        binding = self.add(game.engine, "Leyline Binding", ref="controlled-leyline")
        grounds = self.add(game.engine, "Dueling Grounds", ref="controlled-grounds")
        foreign = self.add(game.engine, "Enchantress's Presence", seat="B", ref="foreign-presence")
        creature = self.add(game.engine, "Grizzly Bears")
        aura = self.add(game.engine, "Rancor", zone="hand")
        naturalize = self.add(game.engine, "Naturalize", seat="B", zone="hand", ref="private-enchantment-removal")
        binding.counters["+1/+1"] = 1
        self.add(game.engine, "Forest", seat="B", ref="forest-one")
        self.add(game.engine, "Forest", seat="B", ref="forest-two")
        for card in (source, presence, binding, grounds, foreign, aura, naturalize):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active="A", mana={"G": 1})
        self.checkpoint(game)
        self.assert_types(game, presence, {"enchantment"})
        self.cast(game, "A", aura, [creature.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for untouched in (source, foreign, aura):
            self.assert_types(game, untouched, {"enchantment"})
        for animated, expected in ((presence, 3), (binding, 7), (grounds, 3)):
            self.assert_types(game, animated, {"enchantment", "creature"})
            self.assertEqual((expected, expected), tuple(game.engine._numeric_stat(animated.object_id, stat)
                                                       for stat in ("power", "toughness")))
        self.assertNotIn(naturalize.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.cast(game, "B", naturalize, [grounds.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[grounds.object_id].zone)
        for restored in (presence, binding):
            self.assert_types(game, restored, {"enchantment"})
        self.replay(game, load=True)

    def test_native_animation_witness_detects_fixed_base_stats(self):
        from quorune.continuous_stat_operations import apply_stat_operation

        def fixed_base(state, operation, context):
            if operation.op == "set_power_toughness_to_mana_value":
                operation = ContinuousOperation("set_power_toughness", (1, 1))
            return apply_stat_operation(state, operation, context)

        with patch("quorune.continuous_stat_operations.apply_stat_operation", new=fixed_base):
            with self.assertRaisesRegex(AssertionError, r"\(3, 3\) != \(1, 1\)"):
                self.test_actual_threshold_counts_auras_and_animates_only_other_controlled_non_auras_with_their_mana_values()

    def test_enchantment_membership_change_is_a_same_layer_threshold_dependency(self):
        handler = ControlledEnchantmentAnimationHandler()
        animation, _ = handler.lower(
            handler.descriptor(), ContinuousEffectSourceContext("source", "source", "A", 2, "ability"),
        )
        target = ContinuousObjectIdentity("changed", "changed-object")
        for before, after in (("Enchantment", "Artifact"), ("Artifact", "Enchantment")):
            with self.subTest(before=before, after=after):
                change = ContinuousEffect(
                    effect_id="change-types", source_id="other-source", layer=Layer.TYPE, sublayer="4", timestamp=9,
                    operations=(ContinuousOperation("set_types", (after,), field="card_types"),),
                    origin=ContinuousEffectOrigin.RESOLUTION, duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                    locked_objects=(target,), applies=ObjectQuerySpec(zones=("battlefield",)),
                )
                views = []
                for name in ("source", "first", "second", "third", "changed"):
                    base = CharacteristicState(name=name, controller="A", card_types={before if name == "changed" else "Enchantment"})
                    context = {"object_id":name,"logical_object_id":f"{name}-object","ref":name,"zone":"battlefield"}
                    views.append((base, context, (animation, change)))
                order = battlefield_type_effect_order(views)
                self.assertEqual((change.effect_id, animation.effect_id), order.effect_ids)
                self.assertEqual(after == "Artifact", animation.effect_id in order.inactive_recipient_scopes)
