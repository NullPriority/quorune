from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
from typing import Mapping
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.carddb import CardDatabase, CardRecord
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.counter_placement import place_counters_on_refs
from quorune.damage import damage_proposal, resolve_damage_batch
from quorune.damage_modifier_state import (
    DamageModifierDuration,
    DamagePreventionShield,
    DamageSubject,
    GainLifePreventionAftermath,
    PreventionMode,
)
from quorune.effect_runtime import dispatch_effect
from quorune.compiler.fixed_counter_trigger_nodes import (
    FIXED_COUNTER_EVENT_TRIGGER_MECHANIC,
    FIXED_COUNTER_EVENT_TRIGGER_TEMPLATE_IDS,
    FIXED_SPELL_CAST_CHARACTERISTIC_MECHANIC,
    FIXED_TYPED_EVENT_EFFECT_TRIGGER_MECHANIC,
    FIXED_TYPED_EVENT_EFFECT_TRIGGER_TEMPLATE_IDS,
    OPTIONAL_COUNTER_PLACEMENT_OPERATION,
    OPTIONAL_FIXED_COUNTER_EVENT_TRIGGER_MECHANIC,
    FixedCounterTriggerBinding,
    FixedCounterTriggerEvent,
    FixedCounterZoneController,
    FixedCounterZoneSubject,
    FixedSpellCastController,
    FixedSpellCastCharacteristicKind,
    FixedSpellCastCharacteristicQuery,
    FixedSpellCastCharacteristicTerm,
    FixedSpellCastQuality,
    FixedSpellCastSubject,
    fixed_counter_trigger_binding,
)
from quorune.compiler.target_effect_corpus_assurance import (
    TargetEffectCorpusCollector,
)
from quorune.deck import DeckLoader
from quorune.kicker import KICKER_CAST_OPTION_ID
from quorune.model import CardInstance, CombatState
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.player_result_events import (
    CardDrawEvent,
    LifeGainEvent,
    PlayerResultEventError,
)
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.rules.entry_return_capability_shapes import (
    fixed_entry_return_node_capabilities,
)
from quorune.semantic_choices.context import (
    SemanticChoiceContext,
    SnapshotSemanticChoiceQuery,
)
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_choices.optional_counter_placement import (
    OptionalCounterPlacementHandler,
)
from quorune.semantic_runtime import LifeChangeIntent
from quorune.trigger_processing import collect_trigger_items, enqueue_trigger_batch
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
TEMPLATE_IDS = set(FIXED_COUNTER_EVENT_TRIGGER_TEMPLATE_IDS)
OPTIONAL_TEMPLATE_IDS = {
    template_id.removesuffix("-v1") + "-optional-v1"
    for template_id in TEMPLATE_IDS
}
ALL_TEMPLATE_IDS = TEMPLATE_IDS | OPTIONAL_TEMPLATE_IDS
FIXED_TYPED_EVENT_TEMPLATE_IDS = set(
    FIXED_TYPED_EVENT_EFFECT_TRIGGER_TEMPLATE_IDS
)


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-counter-event-triggers.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "damage-result-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-counter-event-trigger-cards.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-typed-event-trigger-cards.json",
            ROOT / "tests" / "fixtures" / "typecycling-cards.json",
            ROOT / "tests" / "fixtures" / "kicker-rules-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedCounterEventTriggerCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, text: str, *, type_line: str = "Artifact"):
        return compile_oracle_card(
            CardRecord(
                oracle_id="00000000-0000-4000-8000-000000000001",
                name="Compiler Fixture",
                mana_cost="{2}",
                mana_value=2.0,
                type_line=type_line,
                oracle_text=text,
                power="2" if "Creature" in type_line else None,
                toughness="2" if "Creature" in type_line else None,
                loyalty=None,
                defense=None,
                colors=(),
                color_identity=(),
                keywords=(),
                produced_mana=(),
                layout="normal",
                released_at="2026-08-30",
                legalities={"commander": "legal"},
                faces=(),
                raw={},
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_typed_event_effect_triggers_compile_closed_bodies(self):
        cases = (
            (
                "At the beginning of your upkeep, draw a card.",
                "Artifact",
                "step.begin",
                "draw",
                "fixed-typed-effect-step-trigger-v1",
            ),
            (
                "Landfall — Whenever a land you control enters, create a "
                "1/1 white Human creature token.",
                "Creature — Elemental",
                "land.enter",
                "create_token",
                "fixed-typed-effect-controlled-land-entry-trigger-v1",
            ),
            (
                "Whenever you cast a noncreature spell, surveil 1.",
                "Creature — Human Wizard",
                "spell.cast",
                "surveil",
                "fixed-typed-effect-controller-spell-cast-trigger-v1",
            ),
            (
                "Whenever you gain life, each opponent loses 1 life.",
                "Creature — Avatar",
                "life.gained",
                "lose_life_each_opponent",
                "fixed-typed-effect-controller-life-gain-trigger-v1",
            ),
            (
                "Whenever you draw a card, this creature deals 1 damage "
                "to any target.",
                "Creature — Wizard",
                "card.drawn",
                "damage",
                "fixed-typed-effect-controller-card-draw-trigger-v1",
            ),
            (
                "Whenever you draw your second card each turn, scry 1.",
                "Creature — Faerie",
                "card.second_draw",
                "scry",
                "fixed-typed-effect-controller-second-draw-trigger-v1",
            ),
            (
                "Whenever another creature you control enters, you gain "
                "1 life.",
                "Enchantment",
                "creature.enter",
                "life",
                "fixed-typed-effect-creature-entry-trigger-v1",
            ),
            (
                "Whenever another Vampire you control enters, target "
                "creature gets +1/+1 until end of turn.",
                "Creature — Vampire Knight",
                "permanent.enter",
                "modify_stats_until_end_of_turn",
                "fixed-typed-effect-subtype-entry-trigger-v1",
            ),
            (
                "Whenever a creature dies, draw a card.",
                "Artifact",
                "creature.dies",
                "draw",
                "fixed-typed-effect-creature-death-trigger-v1",
            ),
        )
        for text, type_line, event, operation, template_id in cases:
            with self.subTest(text=text):
                record = replace(
                    self.db.lookup("Scheduled Counter Trigger Fixture"),
                    name="Compiler Fixture",
                    oracle_text=text,
                    type_line=type_line,
                    keywords=(),
                    faces=(),
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                TargetEffectCorpusCollector().observe(record, ir)
                self.assertEqual("exact", ir.status)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertEqual(event, node.event)
                self.assertIn(operation, {
                    str(effect.get("op") or "")
                    for effect in node.effects
                })
                self.assertIn(
                    FIXED_TYPED_EVENT_EFFECT_TRIGGER_MECHANIC,
                    node.mechanics,
                )
                self.assertIn(
                    "trigger.effect.fixed_event",
                    node.capability_dependencies,
                )
                self.assertIn(
                    "trigger.placement.apnap",
                    node.capability_closure,
                )
                programs = [
                    program
                    for program in generated_programs(
                        self.db,
                        record,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id") == template_id
                ]
                self.assertEqual(1, len(programs))
                self.assertTrue(programs[0].capability_closure["trusted"])

        counter = self.compile(
            "At the beginning of your upkeep, put a charge counter on "
            "this artifact."
        )
        counter_node = next(
            value
            for value in counter.faces[0].nodes
            if value.template_id == "fixed-counter-step-trigger-v1"
        )
        self.assertIn(
            FIXED_COUNTER_EVENT_TRIGGER_MECHANIC,
            counter_node.mechanics,
        )
        self.assertNotIn(
            FIXED_TYPED_EVENT_EFFECT_TRIGGER_MECHANIC,
            counter_node.mechanics,
        )

    def test_trigger_ability_words_reuse_existing_typed_owners(self):
        cases = (
            (
                "Keen Senses — When this creature enters, draw a card.",
                "Creature — Bear",
                "permanent.enter.self",
                "draw-controller-v1",
            ),
            (
                "Combat Inspiration — At the beginning of combat on your "
                "turn, target creature you control gets +1/+0 until end of "
                "turn.",
                "Creature — Human Bard",
                "step.begin",
                "fixed-typed-effect-step-trigger-v1",
            ),
            (
                "Flurry of Blows — Whenever you cast your second spell each "
                "turn, put a +1/+1 counter on this creature.",
                "Creature — Human Monk",
                "spell.cast",
                "fixed-counter-spell-cast-trigger-v1",
            ),
            (
                "Constellation — Whenever an enchantment you control enters, "
                "tap target creature an opponent controls.",
                "Creature — Unicorn",
                "enchantment.enter",
                "fixed-typed-effect-enchantment-entry-trigger-v1",
            ),
        )
        for text, type_line, event, template_id in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual("triggered_ability", node.kind)
                self.assertEqual(event, node.event)
                self.assertEqual(template_id, node.template_id)
                self.assertEqual(text, node.text)
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_trigger_ability_word_boundary_fails_closed(self):
        text = "Keen Senses — When this creature enters, draw a card."
        self.assertEqual("exact", self.compile(text).status)
        with patch(
            "quorune.oracle_ir.trigger_ability_word_material_line",
            side_effect=lambda value: value,
        ):
            self.assertNotEqual("exact", self.compile(text).status)

        exclusions = (
            "Threshold — As long as seven cards are in your graveyard, this "
            "creature gets +1/+1.",
            "Heroic — Whenever you cast a spell of the chosen color, draw a "
            "card.",
            "I — Draw a card.",
        )
        for excluded in exclusions:
            with self.subTest(text=excluded):
                self.assertNotEqual(
                    "exact",
                    self.compile(
                        excluded,
                        type_line="Creature — Wizard",
                    ).status,
                )

    def test_closed_cast_and_source_attack_bindings_compile_exactly(self):
        cases = (
            (
                "Whenever you cast an artifact spell, draw a card.",
                "Artifact",
                "spell.cast",
                "fixed-typed-effect-spell-cast-trigger-v1",
                {
                    "all": [
                        {
                            "field": "controller",
                            "op": "eq",
                            "value": "$source.controller",
                        },
                        {
                            "field": "types",
                            "op": "contains_any",
                            "value": ["artifact"],
                        },
                    ]
                },
                "trigger.event.normalized_spell_cast",
            ),
            (
                "Whenever an opponent casts a spell, you gain 1 life.",
                "Artifact",
                "spell.cast",
                "fixed-typed-effect-spell-cast-trigger-v1",
                {
                    "field": "controller",
                    "op": "ne",
                    "value": "$source.controller",
                },
                "trigger.event.normalized_spell_cast",
            ),
            (
                "Whenever a player casts a spell, you gain 1 life.",
                "Artifact",
                "spell.cast",
                "fixed-typed-effect-spell-cast-trigger-v1",
                None,
                "trigger.event.normalized_spell_cast",
            ),
            (
                "Whenever this creature attacks, you gain 1 life.",
                "Creature — Soldier",
                "creature.attacks",
                "fixed-typed-effect-source-attacks-trigger-v1",
                {
                    "field": "card",
                    "op": "eq",
                    "value": "$source.ref",
                },
                "trigger.event.normalized_self_attack",
            ),
        )
        for (
            text,
            type_line,
            event,
            template_id,
            condition,
            event_capability,
        ) in cases:
            with self.subTest(text=text):
                binding = fixed_counter_trigger_binding(text)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(event, binding.event.value)
                self.assertEqual(condition, binding.event_condition)
                self.assertEqual(
                    template_id.replace(
                        "fixed-typed-effect-",
                        "fixed-counter-",
                    ),
                    binding.template_id,
                )
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertEqual(condition, node.event_condition)
                self.assertIn(event_capability, node.capability_dependencies)
                self.assertIn(
                    "trigger.effect.fixed_event",
                    node.capability_dependencies,
                )

        counter = self.compile(
            "Whenever this creature attacks, put a +1/+1 counter on "
            "this creature.",
            type_line="Creature — Soldier",
        )
        counter_node = next(
            value
            for value in counter.faces[0].nodes
            if value.template_id == "fixed-counter-source-attacks-trigger-v1"
        )
        self.assertEqual("exact", counter.status)
        self.assertIn(
            "trigger.event.normalized_self_attack",
            counter_node.capability_dependencies,
        )
        self.assertIn(
            "counter.producer.fixed_event_trigger",
            counter_node.capability_dependencies,
        )

        optional = self.compile(
            "Whenever an opponent casts an artifact spell, you may put a "
            "charge counter on this artifact."
        )
        optional_node = next(
            value
            for value in optional.faces[0].nodes
            if value.template_id
            == "fixed-counter-spell-cast-trigger-optional-v1"
        )
        self.assertEqual("exact", optional.status)
        self.assertIn(
            "counter.producer.optional_fixed_event_trigger",
            optional_node.capability_dependencies,
        )

        with self.assertRaises(ValueError):
            FixedSpellCastSubject(
                controller="source_controller",
                quality=FixedSpellCastQuality.ANY,
            )
        with self.assertRaises(ValueError):
            FixedSpellCastSubject(
                controller=FixedSpellCastController.SOURCE,
                quality="artifact",
            )

    def test_static_spell_cast_characteristic_bindings_compile_exactly(self):
        cases = (
            (
                "Whenever you cast a white spell, draw a card.",
                {
                    "all": [
                        {
                            "field": "controller",
                            "op": "eq",
                            "value": "$source.controller",
                        },
                        {
                            "field": "colors",
                            "op": "contains_any",
                            "value": ["W"],
                        },
                    ]
                },
            ),
            (
                "Whenever an opponent casts a colorless spell, you gain 1 life.",
                {
                    "all": [
                        {
                            "field": "controller",
                            "op": "ne",
                            "value": "$source.controller",
                        },
                        {"field": "colors", "op": "falsy", "value": True},
                    ]
                },
            ),
            (
                "Whenever a player casts a multicolored spell, scry 1.",
                {"field": "colors", "op": "count_gte", "value": 2},
            ),
            (
                "Whenever you cast a legendary or Spirit spell, draw a card.",
                {
                    "all": [
                        {
                            "field": "controller",
                            "op": "eq",
                            "value": "$source.controller",
                        },
                        {
                            "any": [
                                {
                                    "field": "subtypes",
                                    "op": "contains_any",
                                    "value": ["spirit"],
                                },
                                {
                                    "field": "supertypes",
                                    "op": "contains_any",
                                    "value": ["legendary"],
                                },
                            ]
                        },
                    ]
                },
            ),
            (
                "Whenever you cast a Spirit or Arcane spell, surveil 1.",
                {
                    "all": [
                        {
                            "field": "controller",
                            "op": "eq",
                            "value": "$source.controller",
                        },
                        {
                            "any": [
                                {
                                    "field": "subtypes",
                                    "op": "contains_any",
                                    "value": ["arcane"],
                                },
                                {
                                    "field": "subtypes",
                                    "op": "contains_any",
                                    "value": ["spirit"],
                                },
                            ]
                        },
                    ]
                },
            ),
        )
        for text, condition in cases:
            with self.subTest(text=text):
                binding = fixed_counter_trigger_binding(text)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(condition, binding.event_condition)
                self.assertIn(
                    FIXED_SPELL_CAST_CHARACTERISTIC_MECHANIC,
                    binding.event_mechanics,
                )
                ir = self.compile(text)
                self.assertEqual("exact", ir.status)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    == "fixed-typed-effect-spell-cast-characteristic-trigger-v1"
                )
                self.assertEqual(condition, node.event_condition)
                self.assertIn(
                    "trigger.event.normalized_spell_cast",
                    node.capability_dependencies,
                )
                self.assertIn(
                    "trigger.effect.fixed_event",
                    node.capability_dependencies,
                )

        counter = self.compile(
            "Whenever you cast a blue spell, put a +1/+1 counter on "
            "this creature.",
            type_line="Creature — Wizard",
        )
        counter_node = next(
            value
            for value in counter.faces[0].nodes
            if value.template_id
            == "fixed-counter-spell-cast-characteristic-trigger-v1"
        )
        self.assertEqual("exact", counter.status)
        self.assertIn(
            "counter.producer.fixed_event_trigger",
            counter_node.capability_dependencies,
        )
        self.assertIn(
            "trigger.event.normalized_spell_cast",
            counter_node.capability_dependencies,
        )

        query = FixedSpellCastCharacteristicQuery(
            (
                FixedSpellCastCharacteristicTerm(
                    FixedSpellCastCharacteristicKind.SUBTYPE,
                    "Spirit",
                ),
            )
        )
        self.assertEqual("subtypes-spirit", query.terms_any[0].variant)
        with self.assertRaises(ValueError):
            FixedSpellCastCharacteristicQuery(())
        with self.assertRaises(ValueError):
            FixedSpellCastCharacteristicTerm(
                FixedSpellCastCharacteristicKind.COLORLESS,
                "colorless",
            )

    def test_typed_spell_cast_fact_predicates_compile_exactly(self):
        cases = (
            (
                "When you cast this spell, draw a card.",
                "Creature — Eldrazi",
                "stack",
                {"field": "card", "op": "eq", "value": "$source.ref"},
            ),
            (
                "Whenever you cast your second spell each turn, draw a card.",
                "Artifact",
                "battlefield",
                {
                    "field": "caster_spell_number",
                    "op": "eq",
                    "value": 2,
                },
            ),
            (
                "Whenever you cast a creature spell with mana value 5 or "
                "greater, draw a card.",
                "Artifact",
                "battlefield",
                {"field": "mana_value", "op": "gte", "value": 5},
            ),
            (
                "Whenever you cast a spell from anywhere other than your "
                "hand, draw a card.",
                "Artifact",
                "battlefield",
                {"field": "from", "op": "ne", "value": "hand"},
            ),
            (
                "Whenever you cast your first spell during each opponent's "
                "turn, draw a card.",
                "Artifact",
                "battlefield",
                {
                    "field": "active_player",
                    "op": "ne",
                    "value": "$source.controller",
                },
            ),
            (
                "Whenever you cast a kicked spell, draw a card.",
                "Artifact",
                "battlefield",
                {"field": "kicked", "op": "truthy", "value": True},
            ),
            (
                "Whenever you cast a spell with {X} in its mana cost, "
                "draw a card.",
                "Artifact",
                "battlefield",
                {"field": "has_x_cost", "op": "truthy", "value": True},
            ),
            (
                "Whenever you cast a spell you don't own, draw a card.",
                "Artifact",
                "battlefield",
                {
                    "field": "owner",
                    "op": "ne",
                    "value": "$source.controller",
                },
            ),
            (
                "Whenever you cast a green permanent spell, draw a card.",
                "Artifact",
                "battlefield",
                {
                    "field": "colors",
                    "op": "contains_any",
                    "value": ["G"],
                },
            ),
            (
                "Whenever you cast a creature spell that has an Adventure, "
                "draw a card.",
                "Artifact",
                "battlefield",
                {
                    "field": "has_adventure",
                    "op": "truthy",
                    "value": True,
                },
            ),
        )
        for text, type_line, active_zone, leaf in cases:
            with self.subTest(text=text):
                binding = fixed_counter_trigger_binding(text)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(active_zone, binding.active_zone)
                self.assertIn(
                    FIXED_SPELL_CAST_CHARACTERISTIC_MECHANIC,
                    binding.event_mechanics,
                )
                serialized = json.dumps(binding.event_condition, sort_keys=True)
                self.assertIn(json.dumps(leaf, sort_keys=True), serialized)
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.event == "spell.cast"
                )
                self.assertEqual(active_zone, node.active_zone)
                self.assertEqual(binding.event_condition, node.event_condition)

        historic = fixed_counter_trigger_binding(
            "Whenever you cast a historic spell, draw a card."
        )
        self.assertIsNotNone(historic)
        assert historic is not None
        historic_condition = json.dumps(
            historic.event_condition,
            sort_keys=True,
        )
        for value in ("artifact", "legendary", "saga"):
            self.assertIn(value, historic_condition)

    def test_dynamic_spell_cast_characteristic_variants_remain_material(self):
        variants = (
            "Whenever you cast a spell with mana value 3, draw a card.",
            "Whenever you cast a spell that targets a creature, draw a card.",
            "Whenever you cast or copy a Spirit spell, draw a card.",
            "Whenever you cast a Spirit spell, if you control an artifact, draw a card.",
            "Whenever you cast a spell of the chosen color, draw a card.",
            "Whenever you cast a spell with mana value greater than the "
            "number of counters on this artifact, draw a card.",
            "Whenever you play a land or cast a spell, draw a card.",
        )
        for text in variants:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.faces[0].residuals)

    def test_fixed_typed_event_effect_trigger_variants_remain_material(self):
        cases = (
            "At the beginning of your upkeep, if you have no cards in hand, "
            "draw a card.",
            "Whenever an opponent casts or copies a spell, draw a card.",
            "Whenever this creature attacks alone, draw a card.",
            "When you do, draw a card.",
            "At the beginning of your upkeep, choose one —",
        )
        for text in cases:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertFalse(
                    any(
                        node.template_id in FIXED_TYPED_EVENT_TEMPLATE_IDS
                        for node in ir.faces[0].nodes
                    )
                )
                self.assertTrue(ir.faces[0].residuals)

    def test_fixed_source_zone_and_damage_bindings_compile_exactly(self):
        cases = (
            (
                "When this Vehicle enters, draw a card.",
                "Artifact — Vehicle",
                "permanent.enter.self",
                "fixed-typed-effect-source-vehicle-entry-trigger-v1",
                None,
                "trigger.event.normalized_zone_change",
            ),
            (
                "When this Vehicle dies, create a Clue token.",
                "Artifact — Vehicle",
                "creature.dies.self",
                "fixed-typed-effect-source-vehicle-death-trigger-v1",
                None,
                "trigger.event.normalized_zone_change",
            ),
            (
                "When this artifact is put into a graveyard from the "
                "battlefield, draw a card.",
                "Artifact",
                "permanent.graveyard.self",
                "fixed-typed-effect-source-graveyard-trigger-v1",
                None,
                "trigger.event.normalized_zone_change",
            ),
            (
                "Whenever this creature deals combat damage to a player, "
                "draw a card.",
                "Creature — Rogue",
                "damage.dealt.self",
                "fixed-typed-effect-source-combat-damage-player-trigger-v1",
                {
                    "all": [
                        {
                            "field": "target_kind",
                            "op": "eq",
                            "value": "player",
                        },
                        {
                            "field": "combat",
                            "op": "truthy",
                            "value": True,
                        },
                    ]
                },
                "trigger.event.normalized_damage",
            ),
            (
                "Whenever this creature deals combat damage to an opponent, "
                "draw a card.",
                "Creature — Rogue",
                "damage.dealt.self",
                "fixed-typed-effect-source-combat-damage-opponent-trigger-v1",
                {
                    "all": [
                        {
                            "field": "target_kind",
                            "op": "eq",
                            "value": "player",
                        },
                        {
                            "field": "combat",
                            "op": "truthy",
                            "value": True,
                        },
                        {
                            "field": "target",
                            "op": "ne",
                            "value": "$source.controller",
                        },
                    ]
                },
                "trigger.event.normalized_damage",
            ),
            (
                "Whenever this creature deals damage to an opponent, draw "
                "a card.",
                "Creature — Rogue",
                "damage.dealt.self",
                "fixed-typed-effect-source-damage-opponent-trigger-v1",
                {
                    "all": [
                        {
                            "field": "target_kind",
                            "op": "eq",
                            "value": "player",
                        },
                        {
                            "field": "target",
                            "op": "ne",
                            "value": "$source.controller",
                        },
                    ]
                },
                "trigger.event.normalized_damage",
            ),
            (
                "Whenever this creature is dealt damage, you gain 1 life.",
                "Creature — Beast",
                "damage.dealt",
                "fixed-typed-effect-source-dealt-damage-trigger-v1",
                {
                    "field": "target",
                    "op": "eq",
                    "value": "$source.ref",
                },
                "trigger.event.normalized_damage",
            ),
        )
        for (
            text,
            type_line,
            event,
            template_id,
            condition,
            event_capability,
        ) in cases:
            with self.subTest(text=text):
                binding = fixed_counter_trigger_binding(text)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(event, binding.event.value)
                self.assertEqual(condition, binding.event_condition)
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertEqual(event, node.event)
                self.assertEqual(condition, node.event_condition)
                self.assertIn(
                    FIXED_TYPED_EVENT_EFFECT_TRIGGER_MECHANIC,
                    node.mechanics,
                )
                self.assertIn(
                    event_capability,
                    node.capability_dependencies,
                )
                self.assertIn(
                    "trigger.effect.fixed_event",
                    node.capability_dependencies,
                )

    def test_fixed_source_event_near_misses_remain_material(self):
        cases = (
            "When this Vehicle enters, if you control an artifact, draw a "
            "card.",
            "When this Vehicle leaves the battlefield, draw a card.",
            "When this creature is put into a graveyard from the battlefield, "
            "draw a card.",
            "Whenever this creature deals damage to a creature, draw a card.",
            "Whenever this creature deals damage, draw a card.",
            "Whenever equipped creature deals combat damage to a player, draw "
            "a card.",
            "Whenever one or more creatures deal combat damage to a player, "
            "draw a card.",
        )
        for text in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line="Creature — Fixture")
                self.assertNotEqual("exact", ir.status)
                self.assertFalse(
                    any(
                        node.template_id in FIXED_TYPED_EVENT_TEMPLATE_IDS
                        for node in ir.faces[0].nodes
                    )
                )
                self.assertTrue(ir.material_residuals)

    def test_fixed_source_event_capabilities_fail_closed(self):
        cases = (
            (
                "When this Vehicle enters, draw a card.",
                "Artifact — Vehicle",
                "trigger.event.normalized_zone_change",
                "fixed-typed-effect-source-vehicle-entry-trigger-v1",
            ),
            (
                "Whenever this creature deals combat damage to a player, "
                "draw a card.",
                "Creature — Rogue",
                "trigger.event.normalized_damage",
                "fixed-typed-effect-source-combat-damage-player-trigger-v1",
            ),
        )
        for text, type_line, capability_id, template_id in cases:
            with self.subTest(capability=capability_id):
                registry = json.loads(
                    REGISTRY_PATH.read_text(encoding="utf-8")
                )
                dependency = next(
                    row
                    for row in registry["capabilities"]
                    if row["id"] == capability_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["focused source-event mutation"]
                record = replace(
                    self.db.lookup("Scheduled Counter Trigger Fixture"),
                    name="Compiler Fixture",
                    oracle_text=text,
                    type_line=type_line,
                    keywords=(),
                    faces=(),
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=CapabilityRegistry(registry),
                    capability_profile="commander_review",
                )
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)
                self.assertNotEqual("exact", ir.status)

    def test_fixed_typed_event_effect_trigger_dependency_and_compiler_mutation_fail_closed(
        self,
    ):
        text = "At the beginning of your upkeep, draw a card."
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in registry["capabilities"]
            if row["id"] == "trigger.effect.fixed_event"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["focused fixed-event mutation"]
        blocked = compile_oracle_card(
            replace(
                self.db.lookup("Scheduled Counter Trigger Fixture"),
                name="Compiler Fixture",
                oracle_text=text,
                keywords=(),
                faces=(),
            ),
            capability_registry=CapabilityRegistry(registry),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", blocked.status)
        self.assertIn(
            "capability:status:trigger.effect.fixed_event:blocked",
            {
                blocker
                for residual in blocked.faces[0].residuals
                for blocker in residual.blockers
            },
        )

        with patch(
            "quorune.oracle_ir.fixed_typed_event_effect_trigger_node",
            return_value=None,
        ):
            mutated = self.compile(text)
        self.assertNotEqual("exact", mutated.status)
        self.assertFalse(
            any(
                node.template_id in FIXED_TYPED_EVENT_TEMPLATE_IDS
                for node in mutated.faces[0].nodes
            )
        )

    def test_source_named_artifact_entry_player_counter_trigger_compiles_exactly(
        self,
    ):
        card_name = "Gonti's Aether Heart"
        text = (
            "Whenever Gonti's Aether Heart or another artifact you control "
            "enters, you get {E}{E} (two energy counters)."
        )
        self.assertIsNone(fixed_counter_trigger_binding(text))
        binding = fixed_counter_trigger_binding(text, card_name=card_name)
        self.assertIsNotNone(binding)
        assert binding is not None
        self.assertEqual(FixedCounterTriggerEvent.ARTIFACT_ENTER, binding.event)
        self.assertEqual(
            "artifact:source_controller:including_source:any_object",
            binding.variant,
        )
        self.assertEqual(
            {
                "field": "controller",
                "op": "eq",
                "value": "$source.controller",
            },
            binding.event_condition,
        )
        record = replace(
            self.db.lookup("Scheduled Counter Trigger Fixture"),
            name=card_name,
            oracle_text=text,
            type_line="Legendary Artifact",
            keywords=(),
            faces=(),
        )

        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        node = next(
            value
            for value in ir.faces[0].nodes
            if value.template_id == "fixed-counter-artifact-entry-trigger-v1"
        )

        self.assertEqual("exact", ir.status)
        self.assertEqual(
            (
                {
                    "op": "place_player_counters",
                    "subjects": "controller",
                    "counter": "energy",
                    "amount": 2,
                    "source": "$source",
                },
            ),
            node.effects,
        )
        self.assertTrue(
            {
                "counter.producer.fixed_event_trigger",
                "counter.producer.fixed_player_effect",
                "trigger.event.normalized_zone_change",
                "trigger.placement.apnap",
            }.issubset(node.capability_dependencies)
        )
        self.assertIn(
            "counter.placement.quantity_replacement",
            node.capability_closure,
        )
        program = next(
            value
            for value in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if value.provenance.get("template_id")
            == "fixed-counter-artifact-entry-trigger-v1"
        )
        self.assertEqual("trusted", program.trust_level)
        self.assertTrue(program.capability_closure["trusted"])

    def test_single_subtype_entry_counter_triggers_compile_exactly(self):
        cases = (
            (
                "Whenever another Human you control enters, put a +1/+1 "
                "counter on this creature.",
                None,
                "permanent:source_controller:other:any_object:subtype-human",
                {
                    "all": [
                        {
                            "field": "controller",
                            "op": "eq",
                            "value": "$source.controller",
                        },
                        {
                            "field": "subtypes",
                            "op": "contains_any",
                            "value": ["human"],
                        },
                        {
                            "field": "card",
                            "op": "ne",
                            "value": "$source.ref",
                        },
                    ]
                },
                "fixed-counter-subtype-entry-trigger-v1",
            ),
            (
                "Whenever this creature or another Ally you control enters, "
                "you may put a +1/+1 counter on this creature.",
                None,
                "permanent:source_controller:including_source:any_object:subtype-ally",
                {
                    "all": [
                        {
                            "field": "controller",
                            "op": "eq",
                            "value": "$source.controller",
                        },
                        {
                            "any": [
                                {
                                    "field": "card",
                                    "op": "eq",
                                    "value": "$source.ref",
                                },
                                {
                                    "field": "subtypes",
                                    "op": "contains_any",
                                    "value": ["ally"],
                                },
                            ]
                        },
                    ]
                },
                "fixed-counter-subtype-entry-trigger-optional-v1",
            ),
            (
                "Whenever Compiler Fixture or another Elf enters, put a "
                "+1/+1 counter on this creature.",
                "Compiler Fixture",
                "permanent:any:including_source:any_object:subtype-elf",
                {
                    "any": [
                        {
                            "field": "card",
                            "op": "eq",
                            "value": "$source.ref",
                        },
                        {
                            "field": "subtypes",
                            "op": "contains_any",
                            "value": ["elf"],
                        },
                    ]
                },
                "fixed-counter-subtype-entry-trigger-v1",
            ),
        )
        for text, card_name, variant, condition, template_id in cases:
            with self.subTest(text=text):
                binding = fixed_counter_trigger_binding(
                    text,
                    card_name=card_name,
                )
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(
                    FixedCounterTriggerEvent.PERMANENT_ENTER,
                    binding.event,
                )
                self.assertEqual(variant, binding.variant)
                self.assertEqual(condition, binding.event_condition)
                self.assertEqual(
                    template_id.replace("-optional-v1", "-v1"),
                    binding.template_id,
                )

                ir = self.compile(text, type_line="Creature — Soldier")
                self.assertEqual("exact", ir.status)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertEqual("permanent.enter", node.event)
                self.assertEqual(condition, node.event_condition)
                self.assertIn(
                    "trigger.event.normalized_zone_change",
                    node.capability_dependencies,
                )
                self.assertIn(
                    "counter.placement.quantity_replacement",
                    node.capability_closure,
                )

        with self.assertRaises(ValueError):
            FixedCounterZoneSubject(
                "permanent",
                FixedCounterZoneController.ANY,
                subtype="Time Lord",
            )
        with self.assertRaises(ValueError):
            FixedCounterZoneSubject(
                "permanent",
                FixedCounterZoneController.ANY,
                include_source=True,
            )
        with self.assertRaises(ValueError):
            FixedCounterZoneSubject(
                "permanent",
                FixedCounterZoneController.ANY,
                exclude_source=True,
                subtype="Human",
                include_source=True,
            )

    def test_subtype_entry_counter_trigger_dependencies_fail_closed(self):
        text = (
            "Whenever another Human you control enters, put a +1/+1 "
            "counter on this creature."
        )
        for dependency_id in (
            "counter.producer.fixed_event_trigger",
            "counter.placement.quantity_replacement",
            "trigger.event.normalized_zone_change",
            "trigger.placement.apnap",
        ):
            with self.subTest(dependency=dependency_id):
                registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                dependency = next(
                    row
                    for row in registry["capabilities"]
                    if row["id"] == dependency_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["focused subtype dependency mutation"]
                ir = compile_oracle_card(
                    replace(
                        self.db.lookup("Scheduled Counter Trigger Fixture"),
                        name="Subtype Dependency Fixture",
                        oracle_text=text,
                        type_line="Creature — Soldier",
                        keywords=(),
                        faces=(),
                    ),
                    capability_registry=CapabilityRegistry(registry),
                    capability_profile="commander_review",
                )
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    == "fixed-counter-subtype-entry-trigger-v1"
                )
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)
                self.assertNotEqual("exact", ir.status)

        with patch(
            "quorune.compiler.fixed_counter_trigger_nodes._SUBTYPE_ENTRY_TRIGGER"
        ) as grammar:
            grammar.fullmatch.return_value = None
            mutated = self.compile(text, type_line="Creature — Soldier")
        self.assertFalse(
            any(
                node.template_id
                == "fixed-counter-subtype-entry-trigger-v1"
                for node in mutated.faces[0].nodes
            )
        )
        self.assertNotEqual("exact", mutated.status)

    def test_optional_fixed_counter_event_triggers_compile_exactly(self):
        cases = (
            (
                "At the beginning of your upkeep, you may put two charge counters on this artifact.",
                "Artifact",
                "fixed-counter-step-trigger-optional-v1",
            ),
            (
                "Landfall — Whenever a land you control enters, you may put a +1/+1 counter on this creature.",
                "Creature — Elemental",
                "fixed-counter-controlled-land-entry-trigger-optional-v1",
            ),
            (
                "Whenever you cast a noncreature spell, you may put a charge counter on this artifact.",
                "Artifact",
                "fixed-counter-controller-spell-cast-trigger-optional-v1",
            ),
            (
                "Whenever you draw a card, you may put a +1/+1 counter on this creature.",
                "Creature — Snake",
                "fixed-counter-controller-card-draw-trigger-optional-v1",
            ),
            (
                "Whenever another creature you control dies, you may put a +1/+1 counter on this creature.",
                "Creature — Vampire",
                "fixed-counter-creature-death-trigger-optional-v1",
            ),
            (
                "At the beginning of combat on your turn, you may put a +1/+1 counter on target creature you control.",
                "Artifact",
                "fixed-counter-step-trigger-optional-v1",
            ),
        )
        for text, type_line, template_id in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertTrue(node.exact)
                self.assertEqual(
                    OPTIONAL_COUNTER_PLACEMENT_OPERATION,
                    node.effects[0]["op"],
                )
                self.assertEqual("$controller", node.effects[0]["player"])
                self.assertEqual(
                    "place_counters",
                    node.effects[0]["effect"]["op"],
                )
                self.assertIn(
                    OPTIONAL_FIXED_COUNTER_EVENT_TRIGGER_MECHANIC,
                    node.mechanics,
                )
                self.assertNotIn(
                    FIXED_COUNTER_EVENT_TRIGGER_MECHANIC,
                    node.mechanics,
                )
                self.assertTrue(
                    {
                        "counter.producer.optional_fixed_event_trigger",
                        "counter.producer.fixed_effect",
                        "trigger.placement.apnap",
                    }.issubset(node.capability_dependencies)
                )
                self.assertIn(
                    "counter.placement.quantity_replacement",
                    node.capability_closure,
                )

    def test_optional_counter_choice_rejects_malformed_nested_effect(self):
        handler = OptionalCounterPlacementHandler()
        query = SnapshotSemanticChoiceQuery(
            seat_order=("A", "B"),
            active_order=("A", "B"),
        )
        context = SemanticChoiceContext(
            actor="A",
            stack_ref="S1",
            stack_controller="A",
            stack_label="Optional counter fixture",
            source_ref="source",
            card_ref=None,
            semantic_program_id="optional-counter-fixture",
            semantic_program_version=1,
            query=query,
        )
        malformed = {
            "op": OPTIONAL_COUNTER_PLACEMENT_OPERATION,
            "player": "A",
            "effect": {
                "op": "place_counters",
                "card": "source",
                "counter": "charge",
                "amount": True,
                "source": "source",
            },
        }
        with self.assertRaises(SemanticChoiceError):
            handler.prepare(malformed, context)
        malformed["player"] = "B"
        malformed["effect"]["amount"] = 1
        with self.assertRaises(SemanticChoiceError):
            handler.prepare(malformed, context)

    def test_normalized_player_result_events_are_strict_public_values(self):
        draw = CardDrawEvent(
            player="A",
            draw_ordinal=2,
            in_own_draw_step=False,
            draw_step_ordinal=None,
        )
        self.assertTrue(draw.is_second_draw)
        self.assertFalse(draw.is_first_own_draw_step_draw)
        self.assertEqual(
            {
                "player": "A",
                "draw_ordinal": 2,
                "in_own_draw_step": False,
                "draw_step_ordinal": None,
            },
            dict(draw.semantic_context()),
        )
        self.assertNotIn("object", draw.semantic_context())
        self.assertNotIn("card", draw.semantic_context())
        with self.assertRaises(FrozenInstanceError):
            draw.player = "B"
        with self.assertRaises(PlayerResultEventError):
            CardDrawEvent("A", 0, False, None)
        with self.assertRaises(PlayerResultEventError):
            CardDrawEvent("A", 1, True, None)

        gain = LifeGainEvent(
            event_id="life:test:1",
            player="B",
            amount=3,
        )
        self.assertEqual(
            {"player": "B", "amount": 3},
            dict(gain.semantic_context()),
        )
        self.assertNotIn("source", gain.semantic_context())
        with self.assertRaises(FrozenInstanceError):
            gain.amount = 4
        with self.assertRaises(PlayerResultEventError):
            LifeGainEvent("life:test:zero", "B", 0)

    def test_closed_event_bindings_compile_exact_counter_effect_bodies(self):
        expected = (
            (
                "At the beginning of your upkeep, put two charge counters on this artifact.",
                "Artifact",
                FixedCounterTriggerEvent.STEP_BEGIN,
                "your upkeep",
                "fixed-counter-step-trigger-v1",
                "charge",
                2,
                (),
            ),
            (
                "At the beginning of each end step, put a charge counter on this artifact.",
                "Artifact",
                FixedCounterTriggerEvent.STEP_BEGIN,
                "each end step",
                "fixed-counter-step-trigger-v1",
                "charge",
                1,
                (),
            ),
            (
                "Landfall — Whenever a land you control enters, put a +1/+1 counter on this creature.",
                "Creature — Elemental",
                FixedCounterTriggerEvent.CONTROLLED_LAND_ENTER,
                "controlled_land",
                "fixed-counter-controlled-land-entry-trigger-v1",
                "+1/+1",
                1,
                ("trigger-event-normalized-zone-change",),
            ),
            (
                "Landfall — Whenever a land you control enters, put two +1/+1 counters on target creature you control. It gains vigilance until end of turn.",
                "Creature — Elf Soldier",
                FixedCounterTriggerEvent.CONTROLLED_LAND_ENTER,
                "controlled_land",
                "fixed-counter-controlled-land-entry-trigger-v1",
                "+1/+1",
                2,
                ("trigger-event-normalized-zone-change",),
            ),
            (
                "Whenever you cast a noncreature spell, put a +1/+1 counter on this creature.",
                "Creature — Artificer",
                FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST,
                "noncreature",
                "fixed-counter-controller-spell-cast-trigger-v1",
                "+1/+1",
                1,
                ("trigger-event-normalized-spell-cast",),
            ),
            (
                "Whenever you cast an instant or sorcery spell, put a charge counter on this artifact.",
                "Artifact",
                FixedCounterTriggerEvent.CONTROLLER_SPELL_CAST,
                "instant_or_sorcery",
                "fixed-counter-controller-spell-cast-trigger-v1",
                "charge",
                1,
                ("trigger-event-normalized-spell-cast",),
            ),
            (
                "Whenever you gain life, put a +1/+1 counter on this creature.",
                "Creature — Cat Soldier",
                FixedCounterTriggerEvent.CONTROLLER_LIFE_GAIN,
                "controller_life_gain",
                "fixed-counter-controller-life-gain-trigger-v1",
                "+1/+1",
                1,
                ("trigger-event-normalized-life-gain",),
            ),
            (
                "Whenever you gain life, put a +1/+1 counter on target creature you control. It gains indestructible until end of turn.",
                "Creature — Spider Human Hero",
                FixedCounterTriggerEvent.CONTROLLER_LIFE_GAIN,
                "controller_life_gain",
                "fixed-counter-controller-life-gain-trigger-v1",
                "+1/+1",
                1,
                ("trigger-event-normalized-life-gain",),
            ),
            (
                "Whenever you draw a card, put a +1/+1 counter on this creature.",
                "Creature — Snake",
                FixedCounterTriggerEvent.CONTROLLER_CARD_DRAW,
                "controller_card_draw",
                "fixed-counter-controller-card-draw-trigger-v1",
                "+1/+1",
                1,
                ("trigger-event-normalized-card-draw",),
            ),
            (
                "Whenever you draw your second card each turn, put a +1/+1 counter on this creature.",
                "Creature — Faerie Rogue",
                FixedCounterTriggerEvent.CONTROLLER_SECOND_DRAW,
                "controller_second_draw",
                "fixed-counter-controller-second-draw-trigger-v1",
                "+1/+1",
                1,
                ("trigger-event-normalized-card-draw",),
            ),
            (
                "Whenever an artifact you control enters, put a charge counter on this artifact.",
                "Artifact",
                FixedCounterTriggerEvent.ARTIFACT_ENTER,
                "artifact:source_controller:including_source:any_object",
                "fixed-counter-artifact-entry-trigger-v1",
                "charge",
                1,
                ("trigger-event-normalized-zone-change",),
            ),
            (
                "Whenever another nontoken creature you control enters, put a +1/+1 counter on this creature.",
                "Creature — Citizen",
                FixedCounterTriggerEvent.CREATURE_ENTER,
                "creature:source_controller:other:nontoken",
                "fixed-counter-creature-entry-trigger-v1",
                "+1/+1",
                1,
                ("trigger-event-normalized-zone-change",),
            ),
            (
                "Whenever another enchantment you control enters, put a lore counter on this enchantment.",
                "Enchantment",
                FixedCounterTriggerEvent.ENCHANTMENT_ENTER,
                "enchantment:source_controller:other:any_object",
                "fixed-counter-enchantment-entry-trigger-v1",
                "lore",
                1,
                ("trigger-event-normalized-zone-change",),
            ),
            (
                "Whenever a permanent you don't control enters, put a charge counter on this artifact.",
                "Artifact",
                FixedCounterTriggerEvent.PERMANENT_ENTER,
                "permanent:opponent:including_source:any_object",
                "fixed-counter-permanent-entry-trigger-v1",
                "charge",
                1,
                ("trigger-event-normalized-zone-change",),
            ),
            (
                "Whenever this creature or another creature dies, put a +1/+1 counter on each Vampire you control.",
                "Creature — Vampire",
                FixedCounterTriggerEvent.CREATURE_DIES,
                "creature:any:including_source:any_object",
                "fixed-counter-creature-death-trigger-v1",
                "+1/+1",
                1,
                ("trigger-event-normalized-zone-change",),
            ),
            (
                "Whenever a creature an opponent controls dies, put a +1/+1 counter on this creature.",
                "Creature — Vampire",
                FixedCounterTriggerEvent.CREATURE_DIES,
                "creature:opponent:including_source:any_object",
                "fixed-counter-creature-death-trigger-v1",
                "+1/+1",
                1,
                ("trigger-event-normalized-zone-change",),
            ),
        )
        for (
            text,
            type_line,
            event,
            variant,
            template_id,
            counter_name,
            amount,
            event_mechanics,
        ) in expected:
            with self.subTest(text=text):
                binding = fixed_counter_trigger_binding(text)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(event, binding.event)
                self.assertEqual(variant, binding.variant)
                self.assertEqual(template_id, binding.template_id)
                self.assertEqual(event_mechanics, binding.event_mechanics)
                with self.assertRaises(FrozenInstanceError):
                    binding.body = "mutated"

                ir = self.compile(text, type_line=type_line)
                TargetEffectCorpusCollector().observe(
                    replace(
                        self.db.lookup(
                            "Scheduled Counter Trigger Fixture"
                        ),
                        name="Compiler Fixture",
                        oracle_text=text,
                        type_line=type_line,
                        keywords=(),
                        faces=(),
                    ),
                    ir,
                )
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual("triggered_ability", node.kind)
                self.assertEqual(event.value, node.event)
                self.assertEqual(text, text[node.span.start : node.span.end])
                self.assertEqual(counter_name, node.effects[0]["counter"])
                self.assertEqual(amount, node.effects[0]["amount"])
                self.assertIn(
                    FIXED_COUNTER_EVENT_TRIGGER_MECHANIC,
                    node.mechanics,
                )
                self.assertTrue(
                    {
                        "counter.producer.fixed_event_trigger",
                        "trigger.placement.apnap",
                    }.issubset(node.capability_dependencies)
                )
                self.assertTrue(
                    any(
                        dependency.startswith("counter.producer.")
                        and dependency
                        != "counter.producer.fixed_event_trigger"
                        for dependency in node.capability_dependencies
                    )
                )
                programs = [
                    program
                    for program in generated_programs(
                        self.db,
                        replace(
                            self.db.lookup(
                                "Scheduled Counter Trigger Fixture"
                            ),
                            name="Compiler Fixture",
                            oracle_text=text,
                            type_line=type_line,
                            keywords=(),
                            faces=(),
                        ),
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id") == template_id
                ]
                self.assertEqual(1, len(programs))
                self.assertTrue(programs[0].capability_closure["trusted"])

        artifact = fixed_counter_trigger_binding(
            "Whenever an artifact you control enters, put a charge counter on this artifact."
        )
        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual(
            {
                "field": "controller",
                "op": "eq",
                "value": "$source.controller",
            },
            artifact.event_condition,
        )
        death = fixed_counter_trigger_binding(
            "Whenever another nontoken creature you control dies, put a +1/+1 counter on this creature."
        )
        self.assertIsNotNone(death)
        assert death is not None
        self.assertEqual(
            {
                "all": [
                    {
                        "field": "controller",
                        "op": "eq",
                        "value": "$source.controller",
                    },
                    {
                        "field": "card",
                        "op": "ne",
                        "value": "$source.ref",
                    },
                    {"field": "token", "op": "eq", "value": False},
                ]
            },
            death.event_condition,
        )
        any_death = fixed_counter_trigger_binding(
            "Whenever this creature or another creature dies, put a +1/+1 counter on this creature."
        )
        self.assertIsNotNone(any_death)
        assert any_death is not None
        self.assertEqual(
            {
                "field": "token",
                "op": "in",
                "value": [False, True],
            },
            any_death.event_condition,
        )

        with self.assertRaises(ValueError):
            FixedCounterTriggerBinding("step.begin", "your upkeep", "body")
        with self.assertRaises(ValueError):
            FixedCounterZoneSubject(
                "creature",
                "source_controller",
            )
        with self.assertRaises(ValueError):
            FixedCounterTriggerBinding(
                FixedCounterTriggerEvent.CREATURE_DIES,
                "creature:any:including_source:any_object",
                "body",
            )
        with self.assertRaises(ValueError):
            FixedCounterTriggerBinding(
                FixedCounterTriggerEvent.STEP_BEGIN,
                "your upkeep",
                "body",
                FixedCounterZoneSubject(
                    "creature",
                    FixedCounterZoneController.ANY,
                ),
            )

        residual_cases = (
            (
                "Innkeeper's Talent",
                "If you would put one or more counters",
                {
                    "replacement applicability",
                    "self-replacement and prevention ordering",
                },
            ),
            (
                "Invasion of Moag // Bloomwielder Dryads",
                "As a Siege enters",
                {
                    "replacement applicability",
                    "self-replacement and prevention ordering",
                },
            ),
        )
        for name, residual_text, expected_blockers in residual_cases:
            with self.subTest(name=name):
                record = self.db.lookup(name, fuzzy=False)
                compiled = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                trigger_nodes = [
                    node
                    for face in compiled.faces
                    for node in face.nodes
                    if node.template_id in TEMPLATE_IDS
                ]
                self.assertEqual(1, len(trigger_nodes))
                self.assertTrue(trigger_nodes[0].exact)
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(
                    any(
                        residual_text in residual.text
                        for residual in compiled.material_residuals
                    )
                )
                blockers = {
                    blocker
                    for residual in compiled.material_residuals
                    for blocker in residual.blockers
                }
                self.assertGreaterEqual(blockers, expected_blockers)

                trigger_programs = [
                    program
                    for program in generated_programs(
                        self.db,
                        record,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id") in TEMPLATE_IDS
                ]
                self.assertEqual(1, len(trigger_programs))
                self.assertTrue(
                    trigger_programs[0].capability_closure["trusted"]
                )

        spore_flower = self.db.lookup("Spore Flower", fuzzy=False)
        compiled = compile_oracle_card(
            spore_flower,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("partial", compiled.status)
        templates = {
            node.template_id: node
            for face in compiled.faces
            for node in face.nodes
        }
        trigger_nodes = [
            node
            for template_id, node in templates.items()
            if template_id in TEMPLATE_IDS
        ]
        self.assertEqual(1, len(trigger_nodes))
        self.assertTrue(trigger_nodes[0].exact)
        prevention = templates["damage-prevention-all-combat-v1"]
        self.assertFalse(prevention.lowerable)
        self.assertFalse(prevention.exact)
        self.assertEqual(
            "create_damage_prevention_shield",
            prevention.effects[0]["op"],
        )
        self.assertIn(
            "damage.prevention.persistent_amount",
            prevention.capability_dependencies,
        )
        self.assertFalse(
            any(
                "Prevent all combat damage" in residual.text
                for residual in compiled.material_residuals
            )
        )
        self.assertTrue(
            any(
                residual.kind == "cost"
                for residual in compiled.material_residuals
            )
        )

    def test_adjacent_event_and_effect_variants_remain_material(self):
        variants = (
            "Whenever you cast or copy a noncreature spell, put a +1/+1 counter on this creature.",
            "Whenever an opponent casts or copies a noncreature spell, put a +1/+1 counter on this creature.",
            "Whenever this creature attacks alone, put a +1/+1 counter on this creature.",
            "Whenever an opponent gains life, put a +1/+1 counter on this creature.",
            "Whenever you draw your third card each turn, put a +1/+1 counter on this creature.",
            "At the beginning of your upkeep, if you control a creature, put a charge counter on this artifact.",
            "At the beginning of your upkeep, put X charge counters on this artifact.",
            "At the beginning of your upkeep, you may put X charge counters on this artifact.",
            "At the beginning of your upkeep, you may put a charge counter on this artifact. If you do, draw a card.",
            "At the beginning of your upkeep, you may put a charge counter on this artifact, then gain 1 life.",
            "You may put a charge counter on this artifact.",
            "At the beginning of your upkeep, move a charge counter from this artifact onto target creature.",
            "At the beginning of your upkeep, remove a charge counter from this artifact.",
            "Whenever one or more creatures die, put a +1/+1 counter on this creature.",
            "Whenever another creature you control enters or dies, put a +1/+1 counter on this creature.",
            "Whenever another creature you control leaves the battlefield, put a +1/+1 counter on this creature.",
            "Whenever another creature with a counter on it dies, put a +1/+1 counter on this creature.",
            "Whenever another artifact dies, put a charge counter on this artifact.",
            "Whenever this artifact or another creature enters, put a charge counter on this artifact.",
            "Whenever another Human or Zombie you control enters, put a +1/+1 counter on this creature.",
            "Whenever another legendary Human you control enters, put a +1/+1 counter on this creature.",
            "Whenever another human you control enters, put a +1/+1 counter on this creature.",
        )
        for text in variants:
            with self.subTest(text=text):
                ir = self.compile(text, type_line="Creature — Fixture")
                self.assertFalse(
                    any(
                        node.template_id in ALL_TEMPLATE_IDS
                        for node in ir.faces[0].nodes
                    )
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_counter_event_trigger_dependencies_and_compiler_mutation_fail_closed(
        self,
    ):
        cases = (
            (
                "Scheduled Counter Trigger Fixture",
                "counter.producer.fixed_event_trigger",
            ),
            (
                "Scheduled Counter Trigger Fixture",
                "counter.placement.quantity_replacement",
            ),
            (
                "Scheduled Counter Trigger Fixture",
                "trigger.placement.apnap",
            ),
            (
                "Landfall Counter Trigger Fixture",
                "trigger.event.normalized_zone_change",
            ),
            (
                "Creature Death Counter Trigger Fixture",
                "trigger.event.normalized_zone_change",
            ),
            (
                "Noncreature Cast Counter Trigger Fixture",
                "trigger.event.normalized_spell_cast",
            ),
            (
                "Ajani's Pridemate",
                "trigger.event.normalized_life_gain",
            ),
            (
                "Lorescale Coatl",
                "trigger.event.normalized_card_draw",
            ),
        )
        for card_name, dependency_id in cases:
            with self.subTest(card_name=card_name, dependency=dependency_id):
                registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                dependency = next(
                    row
                    for row in registry["capabilities"]
                    if row["id"] == dependency_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["focused dependency mutation"]
                ir = compile_oracle_card(
                    self.db.lookup(card_name),
                    capability_registry=CapabilityRegistry(registry),
                    capability_profile="commander_review",
                )
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id in TEMPLATE_IDS
                )
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)
                self.assertNotEqual("exact", ir.status)

        record = self.db.lookup("Landfall Counter Trigger Fixture")
        with patch(
            "quorune.compiler.fixed_counter_trigger_nodes.fixed_counter_event_trigger_node",
            return_value=None,
        ):
            mutated = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                node.template_id in TEMPLATE_IDS
                for node in mutated.faces[0].nodes
            )
        )
        self.assertNotEqual("exact", mutated.status)

    def test_new_event_bindings_fail_closed_with_event_capability(self):
        cases = (
            (
                "Whenever this creature attacks, you gain 1 life.",
                "Creature — Soldier",
                "trigger.event.normalized_self_attack",
                "fixed-typed-effect-source-attacks-trigger-v1",
            ),
            (
                "Whenever this creature attacks, put a +1/+1 counter on "
                "this creature.",
                "Creature — Soldier",
                "trigger.event.normalized_self_attack",
                "fixed-counter-source-attacks-trigger-v1",
            ),
            (
                "Whenever an opponent casts an artifact spell, draw a card.",
                "Artifact",
                "trigger.event.normalized_spell_cast",
                "fixed-typed-effect-spell-cast-trigger-v1",
            ),
            (
                "Whenever you cast a multicolored spell, draw a card.",
                "Artifact",
                "trigger.event.normalized_spell_cast",
                "fixed-typed-effect-spell-cast-characteristic-trigger-v1",
            ),
        )
        for text, type_line, dependency_id, template_id in cases:
            with self.subTest(text=text, dependency=dependency_id):
                registry = json.loads(
                    REGISTRY_PATH.read_text(encoding="utf-8")
                )
                dependency = next(
                    row
                    for row in registry["capabilities"]
                    if row["id"] == dependency_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["focused event mutation"]
                record = replace(
                    self.db.lookup("Scheduled Counter Trigger Fixture"),
                    name="Compiler Fixture",
                    oracle_text=text,
                    type_line=type_line,
                    keywords=(),
                    faces=(),
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=CapabilityRegistry(registry),
                    capability_profile="commander_review",
                )
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)
                self.assertNotEqual("exact", ir.status)

    def test_optional_counter_event_trigger_dependencies_and_mutations_fail_closed(
        self,
    ):
        record = self.db.lookup("Optional Scheduled Counter Trigger Fixture")
        for dependency_id in (
            "counter.producer.optional_fixed_event_trigger",
            "counter.producer.fixed_effect",
            "counter.placement.quantity_replacement",
            "trigger.placement.apnap",
        ):
            with self.subTest(dependency=dependency_id):
                registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                dependency = next(
                    row
                    for row in registry["capabilities"]
                    if row["id"] == dependency_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["focused dependency mutation"]
                ir = compile_oracle_card(
                    record,
                    capability_registry=CapabilityRegistry(registry),
                    capability_profile="commander_review",
                )
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id in OPTIONAL_TEMPLATE_IDS
                )
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)
                self.assertNotEqual("exact", ir.status)

        with patch(
            "quorune.compiler.fixed_counter_trigger_nodes.OPTIONAL_COUNTER_PLACEMENT_OPERATION",
            "mutated_optional_counter_operation",
        ):
            mutated = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        node = next(
            value
            for value in mutated.faces[0].nodes
            if value.template_id in OPTIONAL_TEMPLATE_IDS
        )
        self.assertFalse(node.exact)
        self.assertTrue(node.residual_ids)
        self.assertNotEqual("exact", mutated.status)


    def test_public_action_trigger_bindings_compile_exactly(self):
        cases = (
            (
                "Whenever a creature you control attacks alone, you may tap "
                "target creature.",
                "Enchantment",
                "creature.attacks",
                "fixed-typed-effect-public-attack-trigger-v1",
            ),
            (
                "Whenever a creature with flying attacks, you may draw a card.",
                "Creature — Sphinx",
                "creature.attacks",
                "fixed-typed-effect-public-attack-trigger-v1",
            ),
            (
                "Whenever a creature you control with defender blocks, you may "
                "gain 2 life.",
                "Creature — Soldier",
                "creature.blocks",
                "fixed-typed-effect-public-block-trigger-v1",
            ),
            (
                "Whenever this creature becomes blocked, you may draw a card.",
                "Creature — Nautilus",
                "creature.becomes_blocked",
                "fixed-typed-effect-public-block-trigger-v1",
            ),
            (
                "When you cycle this card, you may gain 2 life.",
                "Instant",
                "card.cycled.self",
                "fixed-typed-effect-public-cycle-trigger-v1",
            ),
            (
                "Whenever a player cycles a card, you may put a +1/+1 counter "
                "on target creature.",
                "Enchantment",
                "card.cycled",
                "fixed-counter-public-cycle-trigger-optional-v1",
            ),
            (
                "Whenever a permanent is turned face up, you may draw a card.",
                "Creature — Human Wizard",
                "permanent.turned_face_up",
                "fixed-typed-effect-public-face-up-trigger-v1",
            ),
            (
                "When this creature is turned face up, you may draw a card.",
                "Creature — Human Wizard",
                "permanent.turned_face_up",
                "fixed-typed-effect-public-face-up-trigger-v1",
            ),
        )
        for text, type_line, event, template_id in cases:
            with self.subTest(text=text):
                binding = fixed_counter_trigger_binding(text)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(event, binding.event.value)
                ir = self.compile(text, type_line=type_line)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertTrue(node.exact)
                self.assertEqual(event, node.event)
                self.assertIn(
                    "trigger.event.normalized_public_action",
                    node.capability_dependencies,
                )
                if text.startswith("When this"):
                    self.assertEqual(
                        {
                            "field": "card",
                            "op": "eq",
                            "value": "$source.ref",
                        },
                        node.event_condition,
                    )

    def test_public_zone_damage_and_cast_predicates_compile_exactly(self):
        cases = (
            (
                "Whenever another creature you control with power 3 or greater "
                "enters, you may draw a card.",
                "Creature — Beast",
                "creature.enter",
                "fixed-typed-effect-public-zone-trigger-v1",
            ),
            (
                "Whenever this creature or another artifact creature dies, you "
                "may untap target artifact.",
                "Artifact Creature — Scorpion",
                "creature.dies",
                "fixed-typed-effect-public-zone-trigger-v1",
            ),
            (
                "Whenever a creature you control deals combat damage to an "
                "opponent, you may draw a card.",
                "Enchantment",
                "damage.dealt",
                "fixed-typed-effect-public-damage-trigger-v1",
            ),
            (
                "Whenever an opponent draws a card, you may draw two cards.",
                "Creature — Sphinx",
                "card.drawn",
                "fixed-typed-effect-opponent-card-draw-trigger-v1",
            ),
            (
                "Whenever an opponent casts a blue spell during your turn, you "
                "may create a 4/4 green Elemental creature token.",
                "Enchantment",
                "spell.cast",
                "fixed-typed-effect-spell-cast-characteristic-trigger-v1",
            ),
            (
                "Whenever you cast an instant spell during your main phase, you "
                "may return this enchantment to its owner's hand.",
                "Enchantment",
                "spell.cast",
                "fixed-typed-effect-spell-cast-trigger-v1",
            ),
        )
        for text, type_line, event, template_id in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertTrue(node.exact)
                self.assertEqual(event, node.event)

    def test_subtype_death_and_graveyard_wordings_keep_distinct_events(self):
        cases = (
            (
                "Whenever another Goblin dies, draw a card.",
                FixedCounterTriggerEvent.CREATURE_DIES,
                "subtype_goblin_dies",
            ),
            (
                "Whenever another Goblin is put into a graveyard from the "
                "battlefield, draw a card.",
                FixedCounterTriggerEvent.PERMANENT_GRAVEYARD,
                "subtype_goblin_graveyard",
            ),
        )
        conditions = []
        for text, event, variant in cases:
            with self.subTest(text=text):
                binding = fixed_counter_trigger_binding(text)
                self.assertIsNotNone(binding)
                assert binding is not None
                self.assertEqual(event, binding.event)
                self.assertEqual(variant, binding.variant)
                conditions.append(binding.event_condition)

                ir = self.compile(text, type_line="Creature — Goblin")
                self.assertEqual("exact", ir.status)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    == "fixed-typed-effect-public-zone-trigger-v1"
                )
                self.assertEqual(event.value, node.event)
                self.assertEqual(binding.event_condition, node.event_condition)
                self.assertIn(
                    "trigger.event.normalized_zone_change",
                    node.capability_dependencies,
                )
        self.assertEqual(conditions[0], conditions[1])

    def test_public_counter_event_effect_variants_compile_exactly(self):
        cases = (
            (
                "Whenever a land enters, put a +1/+1 counter on this creature.",
                "land.enter",
                "fixed-counter-public-zone-trigger-v1",
            ),
            (
                "Whenever an opponent draws a card, put a +1/+1 counter on this creature.",
                "card.drawn",
                "fixed-counter-opponent-card-draw-trigger-v1",
            ),
            (
                "Whenever another Zombie you control dies, put a +1/+1 counter on this creature.",
                "creature.dies",
                "fixed-counter-public-zone-trigger-v1",
            ),
            (
                "Whenever another Zombie you control dies, you may put a +1/+1 counter on this creature.",
                "creature.dies",
                "fixed-counter-public-zone-trigger-optional-v1",
            ),
            (
                "Whenever another Human you control dies, put a +1/+1 counter on this creature.",
                "creature.dies",
                "fixed-counter-public-zone-trigger-v1",
            ),
        )
        for text, event, template_id in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line="Creature — Fixture")
                self.assertEqual("exact", ir.status)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id == template_id
                )
                self.assertTrue(node.exact)
                self.assertEqual(event, node.event)
                self.assertFalse(ir.material_residuals)

    def test_public_event_near_misses_remain_material(self):
        cases = (
            "Whenever an opponent discards a card, you may draw a card.",
            "Whenever you sacrifice a green creature, you may gain 2 life.",
            "Whenever equipped creature attacks, you may draw a card.",
            "Whenever one or more creatures you control attack, you may draw a card.",
            "Whenever a creature you control becomes the target of a spell, you "
            "may draw a card.",
            "Whenever a creature you control becomes tapped, you may gain 1 life.",
            "Whenever one or more +1/+1 counters are put on this creature, you "
            "may create a 1/1 green Squirrel creature token.",
            "When you cycle this card and when this creature dies, you may draw "
            "a card.",
            "When you do, you may draw a card.",
            "When another creature is turned face up, you may draw a card.",
            "When this creature turns face up, you may draw a card.",
        )
        for text in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line="Creature — Fixture")
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_public_action_event_capability_fails_closed(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in registry["capabilities"]
            if row["id"] == "trigger.event.normalized_public_action"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["focused public action mutation"]
        text = "Whenever a creature you control attacks, you may gain 1 life."
        ir = compile_oracle_card(
            replace(
                self.db.lookup("Scheduled Counter Trigger Fixture"),
                name="Compiler Fixture",
                oracle_text=text,
                type_line="Enchantment",
                keywords=(),
                faces=(),
            ),
            capability_registry=CapabilityRegistry(registry),
            capability_profile="commander_review",
        )
        node = next(
            value
            for value in ir.faces[0].nodes
            if value.template_id
            == "fixed-typed-effect-public-attack-trigger-v1"
        )
        self.assertFalse(node.exact)
        self.assertNotEqual("exact", ir.status)

    def test_source_combat_growth_triggers_compile_exactly_and_fail_closed(self):
        cases = (
            (
                "Whenever this creature attacks, it gets +2/+0 until end of turn.",
                "creature.attacks",
                "fixed-typed-effect-source-attacks-trigger-v1",
                "modify_stats_until_end_of_turn",
                {"field": "card", "op": "eq", "value": "$source.ref"},
            ),
            (
                "Whenever this creature blocks, it gets +0/+2 until end of turn.",
                "creature.blocks",
                "fixed-typed-effect-public-block-trigger-v1",
                "modify_stats_until_end_of_turn",
                {"field": "card", "op": "eq", "value": "$source.ref"},
            ),
            (
                "Whenever this creature becomes blocked, it gets +1/+1 until end of turn.",
                "creature.becomes_blocked",
                "fixed-typed-effect-public-block-trigger-v1",
                "modify_stats_until_end_of_turn",
                {"field": "card", "op": "eq", "value": "$source.ref"},
            ),
            (
                "Whenever this creature blocks a creature with flying, this creature gets +3/+0 until end of turn.",
                "creature.blocks",
                "fixed-typed-effect-public-block-trigger-v1",
                "modify_stats_until_end_of_turn",
                {
                    "all": [
                        {"field": "card", "op": "eq", "value": "$source.ref"},
                        {
                            "field": "blocked_attacker_keywords",
                            "op": "contains_any",
                            "value": ["flying"],
                        },
                    ]
                },
            ),
            (
                "Whenever this creature deals combat damage to a player, put a +1/+1 counter on it.",
                "damage.dealt.self",
                "fixed-counter-source-combat-damage-player-trigger-v1",
                "place_counters",
                {
                    "all": [
                        {"field": "target_kind", "op": "eq", "value": "player"},
                        {"field": "combat", "op": "truthy", "value": True},
                    ]
                },
            ),
        )
        for text, event, template_id, operation, condition in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line="Creature — Test")
                self.assertEqual("exact", ir.status)
                node = ir.faces[0].nodes[0]
                self.assertEqual(event, node.event)
                self.assertEqual(template_id, node.template_id)
                self.assertEqual(operation, node.effects[0]["op"])
                self.assertEqual("$source.zone_object", node.effects[0]["card"])
                self.assertEqual(condition, node.event_condition)
                self.assertEqual(
                    (CURRENT_ABILITY_FRAGMENT_COVERAGE,),
                    node.runtime_coverage,
                )
                self.assertIn("trigger.placement.apnap", node.capability_dependencies)

        exclusions = (
            "Whenever this creature attacks or blocks, it gets +1/+1 until end of turn.",
            "Whenever this creature attacks, it gets +X/+X until end of turn.",
            "Whenever this creature attacks, it gets +0/+0 until end of turn.",
            "Whenever this creature attacks, you may put a +1/+1 counter on it.",
            "Whenever this creature attacks, put two +1/+1 counters on it.",
            "Whenever this creature attacks, put a charge counter on it.",
            "Whenever this creature deals combat damage to an opponent, put a +1/+1 counter on it.",
            "Whenever this creature blocks a creature without flying, this creature gets +3/+0 until end of turn.",
        )
        for text in exclusions:
            with self.subTest(excluded=text):
                self.assertNotEqual(
                    "exact",
                    self.compile(text, type_line="Creature — Test").status,
                )

        with patch(
            "quorune.compiler.fixed_counter_trigger_nodes."
            "fixed_source_combat_growth_effect_template",
            return_value=(None, (), None, ()),
        ):
            mutated = self.compile(
                "Whenever this creature attacks, it gets +2/+0 until end of turn.",
                type_line="Creature — Test",
            )
        self.assertNotEqual("exact", mutated.status)

    def test_fixed_entry_return_requirements_compile_exactly_and_fail_closed(self):
        cases = (
            (
                "When this land enters, return a land you control to its owner's hand.",
                "Land",
                "choose_cards_apnap",
            ),
            (
                "When this creature enters, sacrifice it unless you return another creature you control to its owner's hand.",
                "Creature — Faerie",
                "choose_option",
            ),
            (
                "When a Dragon you control enters, return this enchantment to its owner's hand.",
                "Enchantment",
                "bounce",
            ),
            (
                "When another creature enters, return this creature to its owner's hand.",
                "Creature — Drake",
                "bounce",
            ),
        )
        for text, type_line, operation in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual("permanent.enter", node.event)
                self.assertEqual(
                    "fixed-typed-effect-entry-return-public-zone-trigger-v1",
                    node.template_id,
                )
                self.assertEqual(operation, node.effects[0]["op"])
                self.assertIn(
                    "choice.controller.fixed_return_owner_hand",
                    node.capability_dependencies,
                )
                self.assertEqual(
                    (CURRENT_ABILITY_FRAGMENT_COVERAGE,),
                    node.runtime_coverage,
                )

        exclusions = (
            "When this creature enters, return up to one target creature you control to its owner's hand.",
            "When this creature enters, return each other creature you control to its owner's hand.",
            "When this creature enters, you may return another creature you control to its owner's hand.",
            "When this creature enters, return X creatures you control to their owner's hand.",
            "When this creature enters, return another creature you control to its owner's hand, then draw a card.",
            "When a creature an opponent controls enters, return this creature to its owner's hand.",
        )
        for text in exclusions:
            with self.subTest(excluded=text):
                self.assertNotEqual(
                    "exact",
                    self.compile(text, type_line="Creature — Test").status,
                )

    def test_entry_return_capability_dependency_fails_closed(self):
        text = "When this land enters, return a land you control to its owner's hand."
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        capability = next(
            row
            for row in value["capabilities"]
            if row["id"] == "choice.controller.fixed_return_owner_hand"
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["focused entry-return dependency mutation"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        record = replace(
            self.db.lookup("Generic Entry Land Return Fixture"),
            oracle_text=text,
        )
        ir = compile_oracle_card(
            record,
            capability_registry=registry,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_entry_return_capability_shape_mutants_fail_closed(self):
        ir = self.compile(
            "When this creature enters, sacrifice it unless you return "
            "another creature you control to its owner's hand.",
            type_line="Creature — Faerie",
        )
        node = ir.faces[0].nodes[0]
        arguments = {
            "target_schema": node.target_schema,
            "mechanic_ids": node.mechanics,
        }
        self.assertEqual(
            ("choice.controller.fixed_return_owner_hand",),
            fixed_entry_return_node_capabilities(
                effects=node.effects,
                **arguments,
            ),
        )
        unexpected_field = deepcopy(node.effects[0])
        unexpected_field["unsupported"] = True
        wrong_option = deepcopy(node.effects[0])
        wrong_option["options"][0]["id"] = "decline"
        wrong_fallback = deepcopy(node.effects[0])
        wrong_fallback["then_by_choice"]["sacrifice"] = [
            {"op": "draw", "count": 1}
        ]
        open_return = deepcopy(node.effects[0])
        open_return["then_by_choice"]["return"][0]["predicate"][
            "controller"
        ] = "$actor"
        for mutant in (
            unexpected_field,
            wrong_option,
            wrong_fallback,
            open_return,
        ):
            with self.subTest(mutant=mutant):
                self.assertEqual(
                    (),
                    fixed_entry_return_node_capabilities(
                        effects=(mutant,),
                        **arguments,
                    ),
                )


class FixedCounterEventTriggerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        zone: str,
        controller: str | None = None,
        is_token: bool = False,
    ) -> CardInstance:
        record = self.db.lookup(name)
        current_controller = controller or seat
        public = zone == "battlefield"
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=current_controller,
            zone=zone,
            is_token=is_token,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats) if public else [seat],
            revealed_to=list(engine.seats) if public else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def deck_card(engine, seat: str, name: str) -> CardInstance:
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def register_trigger(self, engine, source: CardInstance):
        programs = [
            program
            for program in generated_programs(
                self.db,
                self.db.by_oracle_id(source.oracle_id),
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id") in ALL_TEMPLATE_IDS
        ]
        self.assertEqual(1, len(programs))
        engine.semantics.put(programs[0])
        return programs[0]

    def register_typed_event_trigger(
        self,
        engine,
        source: CardInstance,
    ):
        programs = [
            program
            for program in generated_programs(
                self.db,
                self.db.by_oracle_id(source.oracle_id),
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            in FIXED_TYPED_EVENT_TEMPLATE_IDS
        ]
        self.assertEqual(1, len(programs))
        engine.semantics.put(programs[0])
        return programs[0]

    @staticmethod
    def _event_condition_fields(condition: Mapping | None) -> set[str]:
        if not isinstance(condition, Mapping):
            return set()
        field = condition.get("field")
        fields = {field} if isinstance(field, str) else set()
        for key in ("all", "any"):
            values = condition.get(key)
            if isinstance(values, list):
                for value in values:
                    fields.update(
                        FixedCounterEventTriggerRuntimeTests._event_condition_fields(
                            value if isinstance(value, Mapping) else None
                        )
                    )
        nested = condition.get("not")
        fields.update(
            FixedCounterEventTriggerRuntimeTests._event_condition_fields(
                nested if isinstance(nested, Mapping) else None
            )
        )
        return fields

    def register_subtype_graveyard_trigger(
        self,
        engine,
        source: CardInstance,
        *,
        event: str,
        qualifier_field: str,
    ):
        programs = [
            program
            for program in generated_programs(
                self.db,
                self.db.by_oracle_id(source.oracle_id),
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            in FIXED_TYPED_EVENT_TEMPLATE_IDS
            and program.event == event
            and qualifier_field
            in self._event_condition_fields(program.event_condition)
        ]
        self.assertEqual(1, len(programs))
        engine.semantics.put(programs[0])
        return programs[0]

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def prepare_noncreature_cast(self, engine) -> CardInstance:
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        card = self.deck_card(engine, "A", "Sol Ring")
        if card.zone != "hand":
            engine.move_card(card.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["C"] += 1
        engine._cast("A", {"card": card.ref, "pay": "auto"})
        return card

    @staticmethod
    def step_context(*, player: str, step: str = "upkeep") -> dict[str, str]:
        return {"phase": "beginning", "step": step, "player": player}

    @staticmethod
    def replacement_options(session, seat: str) -> list[str]:
        decision = StateProjector(
            session.engine.card_db,
            session.state,
        )._decision(f"pilot:{seat}")
        assert decision is not None
        return [option["id"] for option in decision["ctx"]["options"]]

    def finish_replacements(self, session, seat: str) -> None:
        for _ in range(8):
            decision = session.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                return
            result = session.act(
                f"pilot:{seat}",
                {
                    "action_id": "choose",
                    "choices": {
                        "replacement": self.replacement_options(
                            session,
                            seat,
                        )[0]
                    },
                },
            )
            self.assertTrue(result.ok, result.summary)
        self.fail("Fixed counter event-trigger replacement did not converge")

    def assert_player_result_trigger(
        self,
        engine,
        source: CardInstance,
        *,
        event: str,
        amount: int | None = None,
    ) -> None:
        engine._stabilize()
        self.assertTrue(engine.state.stack)
        item = engine.state.stack[-1]
        self.assertEqual(event, item.context["event"])
        self.assertEqual(source.object_id, item.source_object_id)
        if amount is not None:
            self.assertEqual(amount, item.context["amount"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_draw_counter_triggers_use_public_normalized_events(self):
        session = self.session(120007)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Lorescale Coatl",
            ref="draw-counter-source",
            zone="battlefield",
        )
        self.register_trigger(engine, source)
        drawn = engine.state.cards[
            engine.state.players["A"].zones["library"][-1]
        ]

        engine._begin_draw_sequence("A", 1, reason="public draw occurrence")
        engine._stabilize()

        item = engine.state.stack[-1]
        self.assertEqual("card.drawn", item.context["event"])
        self.assertEqual("A", item.context["player"])
        self.assertEqual(1, item.context["draw_ordinal"])
        serialized = json.dumps(item.context, sort_keys=True)
        self.assertNotIn(drawn.ref, serialized)
        self.assertNotIn(drawn.printed_name, serialized)
        self.assertNotIn("object", item.context)
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_draw_and_second_draw_counter_triggers_share_one_batch(self):
        session = self.session(120008, players=4)
        engine = session.engine
        draw_source = self.add_card(
            engine,
            seat="A",
            name="Lorescale Coatl",
            ref="each-draw-counter-source",
            zone="battlefield",
        )
        second_source = self.add_card(
            engine,
            seat="A",
            name="Faerie Vandal",
            ref="second-draw-counter-source",
            zone="battlefield",
        )
        self.register_trigger(engine, draw_source)
        self.register_trigger(engine, second_source)
        turn_key = str(engine.state.turn_sequence)
        engine.state.players["A"].stats.setdefault(
            "cards_drawn_by_turn", {}
        )[turn_key] = 1

        engine._begin_draw_sequence("A", 1, reason="second public draw")
        engine._stabilize()

        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(2, len(batch.items))
        self.assertEqual(
            {"card.drawn", "card.second_draw"},
            {item.normalized_event_id for item in batch.items},
        )
        refs = [
            item["id"]
            for item in engine.state.pending_decision.payload_by_actor["A"][
                "triggers"
            ]
        ]
        ordered = session.act(
            "pilot:A",
            {"action_id": "order", "triggers": refs},
        )
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertEqual(
            {draw_source.object_id, second_source.object_id},
            {item.source_object_id for item in engine.state.stack[-2:]},
        )
        self.resolve_top(engine)
        self.resolve_top(engine)
        self.assertEqual(1, draw_source.counters.get("+1/+1"))
        self.assertEqual(1, second_source.counters.get("+1/+1"))

    def test_life_gain_counter_trigger_uses_replacement_resolved_amount(self):
        session = self.session(120009)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Ajani's Pridemate",
            ref="life-counter-source",
            zone="battlefield",
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.lookup("Boon Reflection"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.add_card(
            engine,
            seat="A",
            name="Boon Reflection",
            ref="life-gain-doubler",
            zone="battlefield",
        )
        self.register_trigger(engine, source)
        before = engine.state.players["A"].life

        dispatch_effect(
            engine,
            {"op": "life", "player": "A", "delta": 1},
            actor="A",
            operation="life",
            reason="replacement-resolved gain",
        )

        self.assertEqual(before + 2, engine.state.players["A"].life)
        self.assert_player_result_trigger(
            engine,
            source,
            event="life.gained",
            amount=2,
        )

    def test_life_gain_counter_trigger_covers_effect_intent_lifelink_and_aftermath(
        self,
    ):
        producers = ("effect", "intent", "lifelink", "aftermath")
        for index, producer in enumerate(producers):
            with self.subTest(producer=producer):
                session = self.session(120010 + index)
                engine = session.engine
                controller = "B" if producer == "aftermath" else "A"
                source = self.add_card(
                    engine,
                    seat=controller,
                    name="Ajani's Pridemate",
                    ref=f"{producer}-life-counter-source",
                    zone="battlefield",
                )
                self.register_trigger(engine, source)
                if producer == "effect":
                    dispatch_effect(
                        engine,
                        {"op": "life", "player": "A", "delta": 1},
                        actor="A",
                        operation="life",
                        reason="immediate represented gain",
                    )
                    amount = 1
                elif producer == "intent":
                    engine.apply_life_change_intent(
                        LifeChangeIntent(
                            actor="A",
                            player="A",
                            amount=2,
                            reason="semantic choice gain",
                        )
                    )
                    amount = 2
                elif producer == "lifelink":
                    lifelink = self.add_card(
                        engine,
                        seat="A",
                        name="Healer's Hawk",
                        ref="lifelink-gain-source",
                        zone="battlefield",
                    )
                    resolve_damage_batch(
                        engine,
                        (
                            damage_proposal(
                                engine,
                                proposal_id="damage:player-result:lifelink",
                                actor="A",
                                source_ref=lifelink.ref,
                                target="B",
                                amount=1,
                                combat=True,
                                reason="represented Lifelink gain",
                            ),
                        ),
                    )
                    amount = 1
                else:
                    damage_source = self.add_card(
                        engine,
                        seat="A",
                        name="Mishra, Eminent One",
                        ref="aftermath-damage-source",
                        zone="battlefield",
                    )
                    engine.state.players["B"].life = 30
                    engine.state.damage_prevention_shields.append(
                        DamagePreventionShield(
                            shield_id="player-result-life-aftermath",
                            source_id="fixture:player-result-life-aftermath",
                            controller="B",
                            subject=DamageSubject(
                                ref="B", kind="player", controller="B"
                            ),
                            mode=PreventionMode.AMOUNT,
                            remaining=2,
                            duration=(
                                DamageModifierDuration.UNTIL_END_OF_TURN
                            ),
                            created_turn_sequence=engine.state.turn_sequence,
                            aftermath=(
                                GainLifePreventionAftermath(
                                    player="B", per_prevented=1
                                ),
                            ),
                        )
                    )
                    resolve_damage_batch(
                        engine,
                        (
                            damage_proposal(
                                engine,
                                proposal_id="damage:player-result:aftermath",
                                actor="A",
                                source_ref=damage_source.ref,
                                target="B",
                                amount=2,
                                combat=False,
                                reason="represented prevention aftermath",
                            ),
                        ),
                    )
                    amount = 2
                self.assert_player_result_trigger(
                    engine,
                    source,
                    event="life.gained",
                    amount=amount,
                )

    def test_player_result_counter_replacement_is_private_and_replays_exactly(
        self,
    ):
        session = self.session(120014, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Ajani's Pridemate",
            ref="private-life-counter-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doubling Season",
            ref="private-life-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doc Samson, Super Psychiatrist",
            ref="private-life-addition",
            zone="battlefield",
        )
        self.register_trigger(engine, source)
        dispatch_effect(
            engine,
            {"op": "life", "player": "C", "delta": 1},
            actor="C",
            operation="life",
            reason="private player-result counter replacement",
        )
        engine._stabilize()
        self.resolve_top(engine)

        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        projected = projector._decision("pilot:C")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.finish_replacements(session, "C")
        self.assertIn(source.counters.get("+1/+1"), {3, 4})
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "player-result-counter-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_player_result_event_dispatch_mutants_are_killed(self):
        draw_session = self.session(120015)
        draw_engine = draw_session.engine
        draw_source = self.add_card(
            draw_engine,
            seat="A",
            name="Lorescale Coatl",
            ref="draw-dispatch-mutant-source",
            zone="battlefield",
        )
        draw_program = self.register_trigger(draw_engine, draw_source)
        with patch(
            "quorune.drawing.transaction.dispatch_card_draw_event",
            return_value=(),
        ):
            draw_engine._begin_draw_sequence(
                "A", 1, reason="draw dispatch mutation"
            )
        draw_engine._stabilize()
        self.assertFalse(
            any(
                item.semantic_key == draw_program.key
                for item in draw_engine.state.stack
            )
        )

        life_session = self.session(120016)
        life_engine = life_session.engine
        life_source = self.add_card(
            life_engine,
            seat="A",
            name="Ajani's Pridemate",
            ref="life-dispatch-mutant-source",
            zone="battlefield",
        )
        life_program = self.register_trigger(life_engine, life_source)
        before = life_engine.state.players["A"].life
        with patch(
            "quorune.effect_runtime.life_effects.dispatch_life_gain_records",
            return_value=(),
        ):
            dispatch_effect(
                life_engine,
                {"op": "life", "player": "A", "delta": 1},
                actor="A",
                operation="life",
                reason="life dispatch mutation",
            )
        life_engine._stabilize()
        self.assertEqual(before + 1, life_engine.state.players["A"].life)
        self.assertFalse(
            any(
                item.semantic_key == life_program.key
                for item in life_engine.state.stack
            )
        )

    def test_cast_counter_trigger_uses_normalized_event(self):
        session = self.session(120001)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Noncreature Cast Counter Trigger Fixture",
            ref="cast-counter-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)

        spell = self.prepare_noncreature_cast(engine)
        engine._stabilize()

        self.assertEqual("stack", spell.zone)
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual("spell.cast", engine.state.stack[-1].context["event"])
        self.assertEqual(
            source.logical_object_id,
            engine.state.stack[-1].context["source_logical_object_id"],
        )
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_cast_relations_and_type_predicates_share_committed_event(self):
        session = self.session(121003, players=4)
        engine = session.engine
        controller_source = self.add_card(
            engine,
            seat="A",
            name="Typed Artifact Cast Draw Trigger Fixture",
            ref="controller-artifact-cast-source",
            zone="battlefield",
        )
        opponent_source = self.add_card(
            engine,
            seat="B",
            name="Typed Opponent Cast Life Trigger Fixture",
            ref="opponent-cast-source",
            zone="battlefield",
        )
        any_source = self.add_card(
            engine,
            seat="C",
            name="Typed Any Cast Life Trigger Fixture",
            ref="any-cast-source",
            zone="battlefield",
        )
        programs = {
            source.ref: self.register_typed_event_trigger(engine, source)
            for source in (controller_source, opponent_source, any_source)
        }
        before_hand = len(engine.state.players["A"].zones["hand"])
        before_life = {
            seat: engine.state.players[seat].life for seat in ("B", "C")
        }

        spell = self.prepare_noncreature_cast(engine)
        engine._stabilize()

        self.assertEqual("stack", spell.zone)
        trigger_items = [
            item
            for item in engine.state.stack
            if item.semantic_key in {program.key for program in programs.values()}
        ]
        self.assertEqual(3, len(trigger_items))
        self.assertEqual(
            {program.key for program in programs.values()},
            {item.semantic_key for item in trigger_items},
        )
        self.assertTrue(
            all(
                item.context["event"] == "spell.cast"
                and item.context["controller"] == "A"
                and item.context["types"] == ["artifact"]
                for item in trigger_items
            )
        )
        for _ in trigger_items:
            self.resolve_top(engine)
        self.assertEqual(
            before_hand + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertEqual(before_life["B"] + 1, engine.state.players["B"].life)
        self.assertEqual(before_life["C"] + 1, engine.state.players["C"].life)

    def test_cast_characteristics_use_one_sealed_stack_snapshot(self):
        source_names = {
            "multicolored": "Typed Multicolored Cast Life Trigger Fixture",
            "colorless": "Typed Colorless Cast Life Trigger Fixture",
            "legendary_or_spirit": (
                "Typed Legendary or Spirit Cast Life Trigger Fixture"
            ),
            "red": "Typed Red Cast Life Trigger Fixture",
        }

        def setup(seed: int):
            session = self.session(seed, players=4)
            engine = session.engine
            programs = {}
            for index, (quality, name) in enumerate(source_names.items()):
                source = self.add_card(
                    engine,
                    seat="B" if quality == "legendary_or_spirit" else "A",
                    name=name,
                    ref=f"{quality}-cast-source-{seed}-{index}",
                    zone="battlefield",
                )
                programs[quality] = self.register_typed_event_trigger(
                    engine,
                    source,
                )
            return session, programs

        def cast_fixture(engine, name: str, *, mana: Mapping[str, int]):
            record = self.db.lookup(name)
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            ):
                engine.semantics.put(program)
            spell = self.add_card(
                engine,
                seat="A",
                name=name,
                ref=f"cast-{name.casefold().replace(' ', '-')}",
                zone="hand",
            )
            engine.state.active_player = "A"
            engine.state.phase = "precombat_main"
            engine.state.step = "main"
            engine.state.priority_player = "A"
            engine.state.priority_passes = []
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            for symbol, amount in mana.items():
                engine.state.players["A"].mana_pool[symbol] += amount
            engine._cast("A", {"card": spell.ref, "pay": "auto"})
            engine._stabilize()
            return spell

        session, programs = setup(121008)
        spell = cast_fixture(
            session.engine,
            "Legendary Spirit Cast Fixture",
            mana={"W": 1, "U": 1},
        )
        items = [
            item
            for item in session.engine.state.stack
            if item.semantic_key in {program.key for program in programs.values()}
        ]
        self.assertEqual("stack", spell.zone)
        self.assertEqual(
            {
                programs["multicolored"].key,
                programs["legendary_or_spirit"].key,
            },
            {item.semantic_key for item in items},
        )
        self.assertEqual(2, len(items))
        for item in items:
            self.assertEqual(["creature"], item.context["types"])
            self.assertEqual(["spirit"], item.context["subtypes"])
            self.assertEqual(["legendary"], item.context["supertypes"])
            self.assertEqual(["W", "U"], item.context["colors"])

        devoid_session, devoid_programs = setup(121009)
        devoid = cast_fixture(
            devoid_session.engine,
            "Devoid Spirit Cast Fixture",
            mana={"C": 2, "R": 1},
        )
        devoid_items = [
            item
            for item in devoid_session.engine.state.stack
            if item.semantic_key
            in {program.key for program in devoid_programs.values()}
        ]
        self.assertEqual("stack", devoid.zone)
        self.assertEqual(
            {
                devoid_programs["colorless"].key,
                devoid_programs["legendary_or_spirit"].key,
            },
            {item.semantic_key for item in devoid_items},
        )
        self.assertEqual(2, len(devoid_items))
        for item in devoid_items:
            self.assertEqual(["creature"], item.context["types"])
            self.assertEqual(["spirit"], item.context["subtypes"])
            self.assertEqual([], item.context["supertypes"])
            self.assertEqual([], item.context["colors"])

    def test_spell_cast_predicates_share_v4_event_and_stack_source(self):
        def cast_fixture(
            current_session,
            name: str,
            ref: str,
            mana: Mapping[str, int],
            response: Mapping[str, object] | None = None,
        ):
            engine = current_session.engine
            record = self.db.lookup(name)
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            ):
                engine.semantics.put(program)
            spell = self.add_card(
                engine,
                seat="A",
                name=name,
                ref=ref,
                zone="hand",
            )
            engine.state.active_player = "A"
            engine.state.phase = "precombat_main"
            engine.state.step = "main"
            engine.state.priority_player = "A"
            engine.state.priority_passes = []
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            for symbol, amount in mana.items():
                engine.state.players["A"].mana_pool[symbol] += amount
            engine._cast(
                "A",
                {
                    "card": spell.ref,
                    "pay": "auto",
                    **dict(response or {}),
                },
            )
            if (
                engine.state.pending_decision is not None
                and engine.state.pending_decision.kind == "trigger.order"
            ):
                refs = [
                    item["id"]
                    for item in engine.state.pending_decision.payload_by_actor[
                        "A"
                    ]["triggers"]
                ]
                ordered = current_session.act(
                    "pilot:A",
                    {"action_id": "order", "triggers": refs},
                )
                self.assertTrue(ordered.ok, ordered.summary)
            return spell

        session = self.session(121010, players=4)
        engine = session.engine
        sources = tuple(
            self.add_card(
                engine,
                seat="A",
                name=name,
                ref=f"cast-predicate-{index}",
                zone="battlefield",
            )
            for index, name in enumerate(
                (
                    "Typed Second Cast Life Trigger Fixture",
                    "Typed Mana Value Cast Life Trigger Fixture",
                    "Typed Historic Cast Life Trigger Fixture",
                ),
                start=1,
            )
        )
        programs = {
            self.register_typed_event_trigger(engine, source).key
            for source in sources
        }
        engine._record_turn_history(
            "spell_cast",
            actor="A",
            object_incarnation="fixture:prior-cast",
            types=("instant",),
        )

        spell = cast_fixture(
            session,
            "Legendary Spirit Cast Fixture",
            "typed-v3-cast",
            {"W": 1, "U": 1},
        )
        items = [
            item for item in engine.state.stack if item.semantic_key in programs
        ]

        self.assertEqual("stack", spell.zone)
        self.assertEqual(programs, {item.semantic_key for item in items})
        self.assertEqual(3, len(items))
        for item in items:
            self.assertEqual(4, item.context["schema_version"])
            self.assertEqual("precombat_main", item.context["phase"])
            self.assertEqual(2.0, item.context["mana_value"])
            self.assertEqual(2, item.context["caster_spell_number"])
            self.assertEqual("A", item.context["owner"])
            self.assertEqual("A", item.context["active_player"])
            self.assertFalse(item.context["kicked"])
            self.assertFalse(item.context["has_x_cost"])
            self.assertFalse(item.context["has_adventure"])

        self_session = self.session(121011, players=4)
        self_engine = self_session.engine
        self_spell = self.add_card(
            self_engine,
            seat="A",
            name="Typed Self Cast Life Trigger Fixture",
            ref="typed-self-cast",
            zone="hand",
        )
        self_program = self.register_typed_event_trigger(
            self_engine,
            self_spell,
        )
        self_engine.state.active_player = "A"
        self_engine.state.phase = "precombat_main"
        self_engine.state.step = "main"
        self_engine.state.priority_player = "A"
        self_engine.state.players["A"].mana_pool["C"] += 1
        self_engine._cast("A", {"card": self_spell.ref, "pay": "auto"})
        self_item = next(
            item
            for item in self_engine.state.stack
            if item.semantic_key == self_program.key
        )
        self.assertEqual("stack", self_spell.zone)
        self.assertEqual(self_spell.ref, self_item.context["card"])
        self.assertEqual(self_spell.object_id, self_item.source_object_id)
        self.assertEqual("stack", self_item.context["source_zone"])

        fact_session = self.session(121012, players=4)
        fact_engine = fact_session.engine
        fact_programs = {}
        for index, (fact, name) in enumerate(
            (
                ("kicked", "Typed Kicked Cast Life Trigger Fixture"),
                ("has_x_cost", "Typed X Cost Cast Life Trigger Fixture"),
                (
                    "has_adventure",
                    "Typed Adventure Cast Life Trigger Fixture",
                ),
            ),
            start=1,
        ):
            source = self.add_card(
                fact_engine,
                seat="A",
                name=name,
                ref=f"typed-positive-fact-{index}",
                zone="battlefield",
            )
            fact_programs[fact] = self.register_typed_event_trigger(
                fact_engine,
                source,
            ).key

        positive_contexts = {}
        for field, name, ref, mana, response in (
            (
                "kicked",
                "Kavu Titan",
                "typed-kicked-cast",
                {"C": 3, "G": 2},
                {"cost_option": KICKER_CAST_OPTION_ID},
            ),
            (
                "has_x_cost",
                "Typed X Cast Fixture",
                "typed-x-cast",
                {"C": 2, "G": 1},
                {"x": 2},
            ),
            (
                "has_adventure",
                "Typed Adventure Cast Fixture // Typed Adventure Effect Fixture",
                "typed-adventure-cast",
                {"C": 2, "B": 1},
                {},
            ),
        ):
            cast_fixture(fact_session, name, ref, mana, response)
            item = next(
                value
                for value in fact_engine.state.stack
                if value.semantic_key == fact_programs[field]
            )
            positive_contexts[field] = dict(item.context)
            fact_engine.state.stack.clear()
            fact_engine.state.pending_trigger_batches.clear()
        for field, context in positive_contexts.items():
            self.assertTrue(context[field])
        self.assertEqual(3.0, positive_contexts["has_x_cost"]["mana_value"])

    def test_main_phase_cast_trigger_optionally_returns_source(self):
        def setup(
            seed: int,
            *,
            phase: str,
            step: str,
            active_player: str = "A",
        ):
            session = self.session(seed, players=4)
            engine = session.engine
            source = self.add_card(
                engine,
                seat="A",
                name="Typed Main Phase Self Return Trigger Fixture",
                ref=f"main-phase-return-{seed}",
                zone="battlefield",
            )
            program = self.register_typed_event_trigger(engine, source)
            spell = self.add_card(
                engine,
                seat="A",
                name="Typed Main Phase Instant Fixture",
                ref=f"main-phase-instant-{seed}",
                zone="hand",
            )
            engine.state.active_player = active_player
            engine.state.phase = phase
            engine.state.step = step
            engine.state.priority_player = "A"
            engine.state.priority_passes = []
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.players["A"].mana_pool["U"] += 1
            engine._cast("A", {"card": spell.ref, "pay": "auto"})
            return session, source, program

        off_phase, off_source, off_program = setup(
            121044,
            phase="ending",
            step="end",
        )
        self.assertFalse(
            any(
                item.semantic_key == off_program.key
                for item in off_phase.engine.state.stack
            )
        )
        self.assertEqual("battlefield", off_source.zone)

        opponent_main, opponent_source, opponent_program = setup(
            121046,
            phase="precombat_main",
            step="main",
            active_player="B",
        )
        self.assertFalse(
            any(
                item.semantic_key == opponent_program.key
                for item in opponent_main.engine.state.stack
            )
        )
        self.assertEqual("battlefield", opponent_source.zone)

        session, source, program = setup(
            121045,
            phase="precombat_main",
            step="main",
        )
        engine = session.engine
        trigger = next(
            item
            for item in engine.state.stack
            if item.semantic_key == program.key
        )
        self.assertEqual("precombat_main", trigger.context["phase"])
        previous_identity = source.logical_object_id

        self.resolve_top(engine)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual("battlefield", source.zone)
        applied = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "apply",
                "reason": "Apply the represented source-return choice.",
            },
        )

        self.assertTrue(applied.ok, applied.summary)
        self.assertEqual("hand", source.zone)
        self.assertNotEqual(previous_identity, source.logical_object_id)
        self.assertIn(
            source.object_id,
            engine.state.players["A"].zones["hand"],
        )

    def test_shroud_and_enchantment_cast_draw_compose(self):
        session = self.session(121007, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="B",
            name="Typed Shroud Enchantment Cast Draw Trigger Fixture",
            ref="shroud-enchantment-cast-source",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)

        legal_target = self.deck_card(engine, "A", "Emry, Lurker of the Loch")
        engine.move_card(
            legal_target.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        reb = self.deck_card(engine, "A", "Red Elemental Blast")
        engine.move_card(reb.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["R"] = 1
        engine.state.priority_player = "A"
        engine._issue_priority("A")
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == reb.ref
        )
        legal_refs = action["target_schema"]["legal_refs"]
        self.assertNotIn(source.ref, legal_refs)
        self.assertIn(legal_target.ref, legal_refs)
        before_rejection = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["destroy"],
                "targets": [source.ref],
                "pay": "manual",
                "payment": {"R": 1},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(
            before_rejection,
            authoritative_state_hash(engine.state),
        )

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "B"
        engine.state.priority_passes = []
        enchantment = self.deck_card(engine, "B", "Mystic Remora")
        engine.move_card(enchantment.object_id, "hand", log=False)
        engine.state.players["B"].mana_pool["U"] += 1
        engine._cast("B", {"card": enchantment.ref, "pay": "auto"})
        engine._stabilize()

        trigger = next(
            item for item in engine.state.stack if item.semantic_key == program.key
        )
        self.assertEqual("spell.cast", trigger.context["event"])
        self.assertEqual(["enchantment"], trigger.context["types"])
        hand_before_draw = len(engine.state.players["B"].zones["hand"])
        library_top = engine.state.players["B"].zones["library"][-1]
        self.resolve_top(engine)
        self.assertEqual(
            hand_before_draw + 1,
            len(engine.state.players["B"].zones["hand"]),
        )
        self.assertIn(library_top, engine.state.players["B"].zones["hand"])
        self.assertEqual("battlefield", source.zone)

    def test_source_attack_trigger_uses_sealed_transition_and_replays(self):
        session = self.session(121004, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="typed-self-attack-source",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        before_life = engine.state.players["A"].life

        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        declared = session.act(
            "pilot:A",
            {"a": "attack", "atk": {source.ref: "B"}},
        )
        self.assertTrue(declared.ok, declared.summary)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("creature.attacks", item.context["event"])
        self.assertEqual(source.ref, item.context["card"])
        self.assertEqual(
            item.context["event_id"],
            item.context["attack_transition"]["transition_id"],
        )
        for seat in engine.active_seats:
            packet = session.packet(f"pilot:{seat}", full=True)
            packet_text = json.dumps(packet, sort_keys=True)
            self.assertNotIn(source.object_id, packet_text)
            self.assertNotIn(source.logical_object_id, packet_text)

        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(before_life + 1, engine.state.players["A"].life)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-self-attack-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_public_action_occurrences_share_typed_batch_owners(self):
        attack_session = self.session(121040, players=4)
        attack_engine = attack_session.engine
        attack_engine.state.active_player = "A"
        attack_engine.state.phase_index = 5
        attack_engine.state.phase = "combat"
        attack_engine.state.step = "declare_attackers"
        attack_engine.state.combat = CombatState()
        observer_a = self.add_card(
            attack_engine,
            seat="A",
            name="Typed Public Attack Trigger Fixture",
            ref="public-attack-observer-a",
            zone="battlefield",
        )
        observer_c = self.add_card(
            attack_engine,
            seat="C",
            name="Typed Public Attack Trigger Fixture",
            ref="public-attack-observer-c",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(attack_engine, observer_a)
        self.register_typed_event_trigger(attack_engine, observer_c)
        attacker = self.add_card(
            attack_engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="public-attacker",
            zone="battlefield",
        )
        attack_engine._issue_attackers()
        declared = attack_session.act(
            "pilot:A",
            {"a": "attack", "atk": {attacker.ref: "B"}},
        )
        self.assertTrue(declared.ok, declared.summary)
        attack_items = [
            item
            for item in attack_engine.state.stack
            if item.semantic_key == program.key
        ]
        self.assertEqual(
            ["A", "C"],
            [item.controller for item in attack_items],
        )
        self.assertEqual(
            {observer_a.object_id, observer_c.object_id},
            {item.source_object_id for item in attack_items},
        )
        attack_item = attack_items[0]
        self.assertEqual("creature.attacks", attack_item.context["event"])
        self.assertEqual(attacker.ref, attack_item.context["card"])
        self.assertEqual("A", attack_item.context["controller"])
        self.assertTrue(attack_item.context["attacking_alone"])

        block_session = self.session(121041, players=2)
        block_engine = block_session.engine
        block_engine.state.active_player = "A"
        block_engine.state.phase_index = 6
        block_engine.state.phase = "combat"
        block_engine.state.step = "declare_blockers"
        block_observer = self.add_card(
            block_engine,
            seat="B",
            name="Typed Public Block Trigger Fixture",
            ref="public-block-observer",
            zone="battlefield",
        )
        block_program = self.register_typed_event_trigger(
            block_engine, block_observer
        )
        attacker_ref = block_engine.create_token(
            "A",
            name="Public block attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        blocker_ref = block_engine.create_token(
            "B",
            name="Public defender blocker",
            characteristics={
                "type_line": "Token Creature — Wall",
                "power": "0",
                "toughness": "4",
                "keywords": ["Defender"],
            },
        )[0]
        block_attacker = block_engine._resolve_object("A", attacker_ref)
        blocker = block_engine._resolve_object("B", blocker_ref)
        block_attacker.attacking = "B"
        block_engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={block_attacker.object_id: "B"},
            defending_players=["B"],
        )
        block_engine._begin_blocker_decisions()
        blocked = block_session.act(
            "pilot:B",
            {"a": "block", "blk": {blocker.ref: block_attacker.ref}},
        )
        self.assertTrue(blocked.ok, blocked.summary)
        block_item = next(
            item
            for item in block_engine.state.stack
            if item.semantic_key == block_program.key
        )
        self.assertEqual("creature.blocks", block_item.context["event"])
        self.assertEqual(blocker.ref, block_item.context["card"])
        self.assertIn("defender", block_item.context["keywords"])
        transition_log = next(
            event
            for event in reversed(block_engine.state.events)
            if event.code == "combat.block_transition"
        )
        self.assertIn(
            block_item.ref,
            transition_log.details["semantic_trigger_refs"],
        )

        becomes_session = self.session(121044, players=2)
        becomes_engine = becomes_session.engine
        becomes_engine.state.active_player = "A"
        becomes_engine.state.phase_index = 6
        becomes_engine.state.phase = "combat"
        becomes_engine.state.step = "declare_blockers"
        becomes_attacker = self.add_card(
            becomes_engine,
            seat="A",
            name="Typed Becomes Blocked Trigger Fixture",
            ref="becomes-blocked-attacker",
            zone="battlefield",
        )
        becomes_program = self.register_typed_event_trigger(
            becomes_engine, becomes_attacker
        )
        blocker_refs = [
            becomes_engine.create_token(
                "B",
                name=f"Becomes blocked witness {index}",
                characteristics={
                    "type_line": "Token Creature — Soldier",
                    "power": "1",
                    "toughness": "3",
                },
            )[0]
            for index in (1, 2)
        ]
        becomes_attacker.attacking = "B"
        becomes_engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={becomes_attacker.object_id: "B"},
            defending_players=["B"],
        )
        becomes_engine._begin_blocker_decisions()
        blockers = [
            becomes_engine._resolve_object("B", ref)
            for ref in blocker_refs
        ]
        becomes_result = becomes_session.act(
            "pilot:B",
            {
                "a": "block",
                "blk": {
                    blocker.ref: becomes_attacker.ref
                    for blocker in blockers
                },
            },
        )
        self.assertTrue(becomes_result.ok, becomes_result.summary)
        becomes_items = [
            item
            for item in becomes_engine.state.stack
            if item.semantic_key == becomes_program.key
        ]
        self.assertEqual(1, len(becomes_items))
        self.assertEqual(
            "creature.becomes_blocked",
            becomes_items[0].context["event"],
        )
        self.assertEqual(
            becomes_attacker.ref,
            becomes_items[0].context["card"],
        )

    def test_public_action_event_dispatch_mutant_is_killed(self):
        session = self.session(121048, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        observer = self.add_card(
            engine,
            seat="C",
            name="Typed Public Attack Trigger Fixture",
            ref="public-action-mutation-observer",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, observer)
        attacker = self.add_card(
            engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="public-action-mutation-attacker",
            zone="battlefield",
        )
        engine._issue_attackers()

        with patch.object(
            engine,
            "_dispatch_semantic_event",
            return_value=[],
        ):
            result = session.act(
                "pilot:A",
                {"a": "attack", "atk": {attacker.ref: "B"}},
            )

        self.assertTrue(result.ok, result.summary)
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

    def test_cycling_trigger_uses_public_hand_snapshot(self):
        session = self.session(121042, players=4)
        engine = session.engine
        observer = self.add_card(
            engine,
            seat="B",
            name="Typed Public Cycling Trigger Fixture",
            ref="public-cycle-observer",
            zone="battlefield",
        )
        observer_program = self.register_typed_event_trigger(engine, observer)
        source = self.deck_card(engine, "A", "Xander's Lounge")
        engine.move_card(source.object_id, "hand", log=False)
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.lookup(source.printed_name),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        engine.state.players["A"].mana_pool["C"] = 3
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()
        action_id = f"activate:{source.ref}:ab3"
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        engine._stabilize()
        self.assertEqual("graveyard", source.zone)
        cycle_item = next(
            item
            for item in engine.state.stack
            if item.semantic_key == observer_program.key
        )
        self.assertEqual("card.cycled", cycle_item.context["event"])
        self.assertEqual(source.ref, cycle_item.context["card"])
        self.assertEqual("A", cycle_item.context["player"])
        self.assertEqual("cycling", cycle_item.context["cycling_kind"])
        serialized = json.dumps(cycle_item.context, sort_keys=True)
        self.assertNotIn(source.object_id, serialized)
        self.assertNotIn(source.logical_object_id, serialized)
        self.assertLess(
            next(
                index
                for index, item in enumerate(engine.state.stack)
                if item.kind == "activated_ability"
                and item.source_object_id == source.object_id
            ),
            engine.state.stack.index(cycle_item),
        )

    def test_typecycling_trigger_uses_public_hand_snapshot(self):
        session = self.session(121043, players=4)
        engine = session.engine
        observer = self.add_card(
            engine,
            seat="B",
            name="Typed Public Cycling Trigger Fixture",
            ref="public-typecycle-observer",
            zone="battlefield",
        )
        observer_program = self.register_typed_event_trigger(engine, observer)
        source = self.add_card(
            engine,
            seat="A",
            name="Ash Barrens",
            ref="public-typecycle-source",
            zone="hand",
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.by_oracle_id(source.oracle_id),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        engine.state.players["A"].mana_pool["C"] = 1
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()

        result = session.act(
            "pilot:A",
            {"action_id": f"activate:{source.ref}:ab2"},
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        cycle_item = next(
            item
            for item in engine.state.stack
            if item.semantic_key == observer_program.key
        )
        self.assertEqual("card.cycled", cycle_item.context["event"])
        self.assertEqual(source.ref, cycle_item.context["card"])
        self.assertEqual("typecycling", cycle_item.context["cycling_kind"])

    def test_face_up_trigger_notifies_public_battlefield_sources(self):
        session = self.session(121045, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="B",
            name="Typed Public Face Up Trigger Fixture",
            ref="public-face-up-source",
            zone="hand",
        )
        program = self.register_typed_event_trigger(engine, source)
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.by_oracle_id(source.oracle_id),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        engine.state.players["B"].mana_pool["C"] = 4
        engine.state.active_player = "B"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.permissions.invalidate_current()
        engine._grant_priority("B")
        engine.pump()
        cast_action = next(
            value
            for value in engine._priority_action_hints("B")["actions"]
            if value["id"] == f"cast-morph:{source.ref}"
        )
        result = session.act("pilot:B", {"action_id": cast_action["id"]})
        self.assertTrue(result.ok, result.summary)
        for _ in range(12):
            if source.zone != "stack":
                break
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", source.zone)
        self.assertTrue(source.face_down)
        action = next(
            value
            for value in engine._priority_action_hints("B")["actions"]
            if value["id"] == f"turn-face-up:{source.ref}"
        )

        result = session.act("pilot:B", {"action_id": action["id"]})

        self.assertTrue(result.ok, result.summary)
        self.assertFalse(source.face_down)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("permanent.turned_face_up", item.context["event"])
        self.assertEqual(source.ref, item.context["card"])
        self.assertEqual("B", item.context["controller"])

    def test_public_zone_trigger_uses_sealed_entry_power(self):
        session = self.session(121046, players=4)
        engine = session.engine
        observer = self.add_card(
            engine,
            seat="A",
            name="Typed Public Power Entry Trigger Fixture",
            ref="public-power-observer",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, observer)

        engine.create_token(
            "A",
            name="Low-power entry witness",
            characteristics={
                "type_line": "Token Creature — Beast",
                "power": "2",
                "toughness": "2",
            },
        )
        engine._stabilize()
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

        high_ref = engine.create_token(
            "A",
            name="High-power entry witness",
            characteristics={
                "type_line": "Token Creature — Beast",
                "power": "3",
                "toughness": "3",
            },
        )[0]
        engine._stabilize()
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("creature.enter", item.context["event"])
        self.assertEqual(high_ref, item.context["card"])
        self.assertEqual(3, item.context["power"])

    def test_public_damage_trigger_uses_committed_damage_occurrence(self):
        session = self.session(121047, players=4)
        engine = session.engine
        observer = self.add_card(
            engine,
            seat="A",
            name="Typed Public Damage Trigger Fixture",
            ref="public-damage-observer",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, observer)
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="public-damage-source",
            zone="battlefield",
        )

        resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="public-damage:noncombat",
                    actor="A",
                    source_ref=source.ref,
                    target="B",
                    amount=1,
                    combat=False,
                    reason="public damage negative witness",
                ),
            ),
        )
        engine._stabilize()
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

        resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="public-damage:combat",
                    actor="A",
                    source_ref=source.ref,
                    target="B",
                    amount=1,
                    combat=True,
                    reason="public damage positive witness",
                ),
            ),
        )
        engine._stabilize()
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("damage.dealt", item.context["event"])
        self.assertEqual(source.ref, item.context["card"])
        self.assertEqual("A", item.context["source_controller"])
        self.assertEqual("B", item.context["target"])
        self.assertTrue(item.context["combat"])

    def test_public_action_occurrences_replay_exactly(self):
        session = self.session(121043, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        observer = self.add_card(
            engine,
            seat="A",
            name="Typed Public Attack Trigger Fixture",
            ref="public-replay-observer",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, observer)
        attacker = self.add_card(
            engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="public-replay-attacker",
            zone="battlefield",
        )
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {"a": "attack", "atk": {attacker.ref: "B"}},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-public-action-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_attack_counter_trigger_uses_replacement_owner(self):
        session = self.session(121006)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="A",
            name="Source Attack Counter Trigger Fixture",
            ref="counter-self-attack-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)

        engine._issue_attackers()
        declared = session.act(
            "pilot:A",
            {"a": "attack", "atk": {source.ref: "B"}},
        )
        self.assertTrue(declared.ok, declared.summary)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("creature.attacks", item.context["event"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_source_attack_event_dispatch_mutant_is_killed(self):
        session = self.session(121005)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Self Attack Life Trigger Fixture",
            ref="mutated-self-attack-source",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)

        engine._issue_attackers()
        with patch.object(
            engine,
            "_dispatch_semantic_event",
            return_value=[],
        ):
            declared = session.act(
                "pilot:A",
                {"a": "attack", "atk": {source.ref: "B"}},
            )
        self.assertTrue(declared.ok, declared.summary)
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

    def test_source_combat_growth_attack_uses_current_ability_and_replays(self):
        session = self.session(121060, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="B",
            name="Generic Attack Growth Trigger Fixture",
            ref="source-combat-growth-attacker",
            zone="battlefield",
        )
        engine.change_control(
            source.object_id,
            "A",
            reason="source combat growth control witness",
        )
        source.temporary_keywords.append("Haste")
        program = self.register_typed_event_trigger(engine, source)

        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        declared = session.act(
            "pilot:A",
            {"a": "attack", "atk": {source.ref: "B"}},
        )
        self.assertTrue(declared.ok, declared.summary)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("A", item.controller)
        self.assertEqual("creature.attacks", item.context["event"])
        self.assertEqual(
            source.logical_object_id,
            item.context["source_logical_object_id"],
        )
        self.assertEqual(2, engine._numeric_stat(source.object_id, "power"))
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(4, engine._numeric_stat(source.object_id, "power"))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "source-combat-growth-attack"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        removed_session = self.session(121061)
        removed_engine = removed_session.engine
        removed_engine.state.active_player = "A"
        removed_engine.state.phase_index = 5
        removed_engine.state.phase = "combat"
        removed_engine.state.step = "declare_attackers"
        removed_engine.state.combat = CombatState()
        removed = self.add_card(
            removed_engine,
            seat="A",
            name="Generic Attack Growth Trigger Fixture",
            ref="source-combat-growth-removed",
            zone="battlefield",
        )
        removed_program = self.register_typed_event_trigger(
            removed_engine,
            removed,
        )
        commit_continuous_effect(
            removed_engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-source-combat-growth",
                source_id="fixture:remove-source-combat-growth-owner",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=removed_engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("battlefield",)),
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=removed.object_id,
                        logical_object_id=removed.logical_object_id,
                    ),
                ),
            ),
        )
        self.assertEqual(
            [],
            removed_engine._effective_card_data(removed)["ability_fragments"],
        )
        removed_engine._issue_attackers()
        removed_result = removed_session.act(
            "pilot:A",
            {"a": "attack", "atk": {removed.ref: "B"}},
        )
        self.assertTrue(removed_result.ok, removed_result.summary)
        self.assertFalse(
            any(
                item.semantic_key == removed_program.key
                for item in removed_engine.state.stack
            )
        )

    def test_source_combat_growth_trigger_controller_is_stable_after_stack_placement(
        self,
    ):
        session = self.session(121069, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="B",
            name="Generic Attack Growth Trigger Fixture",
            ref="source-combat-growth-controller-lock",
            zone="battlefield",
        )
        engine.change_control(
            source.object_id,
            "A",
            reason="source combat growth pre-event controller witness",
        )
        source.temporary_keywords.append("Haste")
        program = self.register_typed_event_trigger(engine, source)

        engine._issue_attackers()
        declared = session.act(
            "pilot:A",
            {"a": "attack", "atk": {source.ref: "B"}},
        )
        self.assertTrue(declared.ok, declared.summary)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("A", item.controller)

        engine.change_control(
            source.object_id,
            "C",
            reason="source combat growth post-placement controller witness",
        )

        self.assertEqual("C", source.controller)
        self.assertEqual("A", item.controller)
        self.resolve_top(engine)
        self.assertEqual(4, engine._numeric_stat(source.object_id, "power"))

    def test_source_combat_growth_block_bindings_use_sealed_participants(self):
        cases = (
            (
                "Generic Block Growth Trigger Fixture",
                (),
                True,
                "creature.blocks",
                "toughness",
                4,
            ),
            (
                "Generic Flying Block Growth Trigger Fixture",
                ("Flying",),
                True,
                "creature.blocks",
                "power",
                4,
            ),
            (
                "Generic Flying Block Growth Trigger Fixture",
                (),
                False,
                "creature.blocks",
                "power",
                1,
            ),
        )
        for index, (
            fixture,
            attacker_keywords,
            should_trigger,
            event,
            stat,
            expected,
        ) in enumerate(cases):
            with self.subTest(fixture=fixture, keywords=attacker_keywords):
                session = self.session(121062 + index)
                engine = session.engine
                engine.state.active_player = "A"
                engine.state.phase_index = 6
                engine.state.phase = "combat"
                engine.state.step = "declare_blockers"
                source = self.add_card(
                    engine,
                    seat="B",
                    name=fixture,
                    ref=f"source-combat-growth-blocker-{index}",
                    zone="battlefield",
                )
                program = self.register_typed_event_trigger(engine, source)
                attacker_ref = engine.create_token(
                    "A",
                    name=f"Source combat growth attacker {index}",
                    characteristics={
                        "type_line": "Token Creature — Test",
                        "power": "2",
                        "toughness": "2",
                        "keywords": list(attacker_keywords),
                    },
                )[0]
                attacker = engine._resolve_object("A", attacker_ref)
                attacker.attacking = "B"
                engine.state.combat = CombatState(
                    attackers_declared=True,
                    had_attacking_creature=True,
                    attackers={attacker.object_id: "B"},
                    defending_players=["B"],
                )
                engine._begin_blocker_decisions()
                result = session.act(
                    "pilot:B",
                    {"a": "block", "blk": {source.ref: attacker.ref}},
                )
                self.assertTrue(result.ok, result.summary)
                matching = [
                    item
                    for item in engine.state.stack
                    if item.semantic_key == program.key
                ]
                self.assertEqual(should_trigger, bool(matching))
                if not should_trigger:
                    self.assertEqual(
                        expected,
                        engine._numeric_stat(source.object_id, stat),
                    )
                    continue
                item = matching[0]
                self.assertEqual(event, item.context["event"])
                self.assertEqual(
                    [value.casefold() for value in attacker_keywords],
                    item.context["blocked_attacker_keywords"],
                )
                self.resolve_top(engine)
                self.assertEqual(
                    expected,
                    engine._numeric_stat(source.object_id, stat),
                )

        session = self.session(121065)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 6
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Becomes Blocked Growth Trigger Fixture",
            ref="source-combat-growth-becomes-blocked",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        blocker_ref = engine.create_token(
            "B",
            name="Source combat growth blocking witness",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "3",
            },
        )[0]
        blocker = engine._resolve_object("B", blocker_ref)
        source.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={source.object_id: "B"},
            defending_players=["B"],
        )
        engine._begin_blocker_decisions()
        result = session.act(
            "pilot:B",
            {"a": "block", "blk": {blocker.ref: source.ref}},
        )
        self.assertTrue(result.ok, result.summary)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("creature.becomes_blocked", item.context["event"])
        self.resolve_top(engine)
        self.assertEqual(3, engine._numeric_stat(source.object_id, "power"))
        self.assertEqual(3, engine._numeric_stat(source.object_id, "toughness"))

    def test_gaining_flying_after_block_declaration_does_not_create_trigger(self):
        session = self.session(121070)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 6
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        source = self.add_card(
            engine,
            seat="B",
            name="Generic Flying Block Growth Trigger Fixture",
            ref="source-combat-growth-gain-flying",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        attacker_ref = engine.create_token(
            "A",
            name="Post-declaration flying attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        attacker = engine._resolve_object("A", attacker_ref)
        attacker.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
        )
        engine._begin_blocker_decisions()
        result = session.act(
            "pilot:B",
            {"a": "block", "blk": {source.ref: attacker.ref}},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

        attacker.temporary_keywords.append("Flying")
        engine._stabilize()

        self.assertIn("flying", engine._combat_keywords(attacker))
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

    def test_losing_flying_after_block_declaration_does_not_remove_existing_trigger(
        self,
    ):
        session = self.session(121071)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 6
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        source = self.add_card(
            engine,
            seat="B",
            name="Generic Flying Block Growth Trigger Fixture",
            ref="source-combat-growth-lose-flying",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        attacker_ref = engine.create_token(
            "A",
            name="Declaration-time flying attacker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
            temporary_keywords=("Flying",),
        )[0]
        attacker = engine._resolve_object("A", attacker_ref)
        attacker.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            defending_players=["B"],
        )
        engine._begin_blocker_decisions()
        result = session.act(
            "pilot:B",
            {"a": "block", "blk": {source.ref: attacker.ref}},
        )
        self.assertTrue(result.ok, result.summary)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual(["flying"], item.context["blocked_attacker_keywords"])

        attacker.temporary_keywords.clear()

        self.assertNotIn("flying", engine._combat_keywords(attacker))
        self.assertEqual("B", item.controller)
        self.resolve_top(engine)
        self.assertEqual(4, engine._numeric_stat(source.object_id, "power"))

    def test_source_combat_growth_damage_requires_committed_player_damage(self):
        session = self.session(121066, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Combat Damage Growth Trigger Fixture",
            ref="source-combat-growth-damage",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)
        recipient_ref = engine.create_token(
            "B",
            name="Source combat growth permanent recipient",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "4",
            },
        )[0]
        for index, (target, combat) in enumerate(
            (("B", False), (recipient_ref, True))
        ):
            resolve_damage_batch(
                engine,
                (
                    damage_proposal(
                        engine,
                        proposal_id=f"source-combat-growth-negative:{index}",
                        actor="A",
                        source_ref=source.ref,
                        target=target,
                        amount=1,
                        combat=combat,
                        reason="source combat growth negative witness",
                    ),
                ),
            )
            engine._stabilize()
            self.assertFalse(
                any(item.semantic_key == program.key for item in engine.state.stack)
            )

        engine.state.damage_prevention_shields.append(
            DamagePreventionShield(
                shield_id="source-combat-growth-prevention",
                source_id="fixture:source-combat-growth-prevention",
                controller="B",
                subject=DamageSubject(ref="B", kind="player", controller="B"),
                mode=PreventionMode.AMOUNT,
                remaining=1,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                created_turn_sequence=engine.state.turn_sequence,
            )
        )
        resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="source-combat-growth-prevented",
                    actor="A",
                    source_ref=source.ref,
                    target="B",
                    amount=1,
                    combat=True,
                    reason="source combat growth prevented witness",
                ),
            ),
        )
        engine._stabilize()
        self.assertFalse(
            any(item.semantic_key == program.key for item in engine.state.stack)
        )

        resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="source-combat-growth-positive",
                    actor="A",
                    source_ref=source.ref,
                    target="C",
                    amount=1,
                    combat=True,
                    reason="source combat growth committed witness",
                ),
            ),
        )
        engine._stabilize()
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("damage.dealt", item.context["event"])
        self.assertEqual("player", item.context["target_kind"])
        self.assertTrue(item.context["combat"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_combat_damage_growth_triggers_in_each_positive_double_strike_damage_step(
        self,
    ):
        session = self.session(121072)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 7
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Combat Damage Growth Trigger Fixture",
            ref="source-combat-growth-double-strike",
            zone="battlefield",
        )
        source.temporary_keywords.append("Double strike")
        source.attacking = "B"
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={source.object_id: "B"},
            defending_players=["B"],
        )
        program = self.register_trigger(engine, source)

        engine._begin_combat_damage()
        first = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual(1, first.context["damage_step"])
        self.assertTrue(first.context["first_strike_step"])
        self.assertEqual(1, first.context["amount"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

        engine._advance_step()

        second = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual(2, second.context["damage_step"])
        self.assertTrue(second.context["first_strike_step"])
        self.assertEqual(2, second.context["amount"])
        self.assertNotEqual(first.ref, second.ref)
        self.resolve_top(engine)
        self.assertEqual(2, source.counters.get("+1/+1"))
        self.assertEqual(37, engine.state.players["B"].life)

    def test_combat_damage_growth_triggers_once_for_positive_trample_player_damage(
        self,
    ):
        session = self.session(121073)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 7
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Combat Damage Growth Trigger Fixture",
            ref="source-combat-growth-trample",
            zone="battlefield",
        )
        source.temporary_keywords.append("Trample")
        place_counters_on_refs(
            engine,
            actor="A",
            object_refs=(source.ref,),
            counter_name="+1/+1",
            amount=3,
            reason="source combat growth trample fixture",
        )
        blocker_ref = engine.create_token(
            "B",
            name="Source combat growth trample blocker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "2",
            },
        )[0]
        blocker = engine._resolve_object("B", blocker_ref)
        source.attacking = "B"
        blocker.blocking = source.object_id
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={source.object_id: "B"},
            defending_players=["B"],
            blockers={source.object_id: [blocker.object_id]},
        )
        program = self.register_trigger(engine, source)
        engine._begin_combat_damage()

        result = session.act(
            "pilot:A",
            {
                "a": "dmg",
                "assignments": [
                    {"source": source.ref, "target": blocker.ref, "amount": 2},
                    {"source": source.ref, "target": "B", "amount": 2},
                ],
            },
        )

        self.assertTrue(result.ok, result.summary)
        matching = [
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        ]
        self.assertEqual(1, len(matching))
        self.assertEqual("B", matching[0].context["player"])
        self.assertEqual(2, matching[0].context["amount"])
        self.assertEqual(2, matching[0].context["assigned_amount"])
        self.resolve_top(engine)
        self.assertEqual(4, source.counters.get("+1/+1"))
        self.assertEqual(38, engine.state.players["B"].life)

    def test_combat_damage_to_nonplayer_permanents_does_not_satisfy_player_binding(
        self,
    ):
        for index, kind in enumerate(("planeswalker", "battle")):
            with self.subTest(kind=kind):
                session = self.session(121074 + index, players=3)
                engine = session.engine
                engine.state.active_player = "A"
                engine.state.phase_index = 7
                engine.state.phase = "combat"
                engine.state.step = "combat_damage"
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Generic Combat Damage Growth Trigger Fixture",
                    ref=f"source-combat-growth-{kind}",
                    zone="battlefield",
                )
                if kind == "planeswalker":
                    target_ref = engine.create_token(
                        "B",
                        name="Source combat growth planeswalker target",
                        characteristics={
                            "type_line": "Token Planeswalker — Test",
                            "loyalty": "5",
                        },
                    )[0]
                else:
                    target_ref = engine.create_token(
                        "C",
                        name="Source combat growth battle target",
                        battle_protector="B",
                        characteristics={
                            "type_line": "Token Battle — Siege",
                            "defense": "5",
                        },
                    )[0]
                target = engine._resolve_object(
                    "A", target_ref, zones={"battlefield"}
                )
                source.attacking = target.ref
                engine.state.combat = CombatState(
                    attackers_declared=True,
                    blockers_declared=True,
                    had_attacking_creature=True,
                    attackers={source.object_id: target.ref},
                    attack_target_context={
                        source.object_id: {
                            "target": target.ref,
                            "kind": kind,
                            "defending_player": "B",
                            "logical_object_id": target.logical_object_id,
                        }
                    },
                    defending_players=["B"],
                )
                program = self.register_trigger(engine, source)

                engine._begin_combat_damage()

                self.assertFalse(
                    any(
                        item.semantic_key == program.key
                        for item in engine.state.stack
                    )
                )
                damage_event = next(
                    event
                    for event in reversed(engine.state.events)
                    if event.code == "combat.damage"
                )
                self.assertEqual(
                    target.ref,
                    damage_event.details["damage_events"][0]["target"],
                )
                self.assertEqual(
                    "permanent",
                    damage_event.details["damage_events"][0]["target_kind"],
                )

    def test_combat_damage_growth_trigger_is_created_when_source_dies_in_same_damage_batch(
        self,
    ):
        session = self.session(121076)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 7
        engine.state.phase = "combat"
        engine.state.step = "combat_damage"
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Combat Damage Growth Trigger Fixture",
            ref="source-combat-growth-lethal",
            zone="battlefield",
        )
        source.temporary_keywords.append("Trample")
        place_counters_on_refs(
            engine,
            actor="A",
            object_refs=(source.ref,),
            counter_name="+1/+1",
            amount=1,
            reason="source combat growth simultaneous lethal fixture",
        )
        blocker_ref = engine.create_token(
            "B",
            name="Source combat growth lethal blocker",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "1",
            },
        )[0]
        blocker = engine._resolve_object("B", blocker_ref)
        source.attacking = "B"
        blocker.blocking = source.object_id
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={source.object_id: "B"},
            defending_players=["B"],
            blockers={source.object_id: [blocker.object_id]},
        )
        program = self.register_trigger(engine, source)
        engine._begin_combat_damage()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        source_logical_at_damage = source.logical_object_id

        result = session.act(
            "pilot:A",
            {
                "a": "dmg",
                "assignments": [
                    {"source": source.ref, "target": blocker.ref, "amount": 1},
                    {"source": source.ref, "target": "B", "amount": 1},
                ],
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", source.zone)
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("A", item.controller)
        self.assertEqual("A", item.context["source_controller"])
        self.assertEqual("battlefield", item.context["source_zone"])
        self.assertEqual(
            source_logical_at_damage,
            item.context["source_logical_object_id"],
        )
        self.assertNotEqual(source_logical_at_damage, source.logical_object_id)
        self.assertEqual(1, item.context["amount"])

        for _ in range(8):
            if not any(
                value.semantic_key == program.key
                for value in engine.state.stack
            ):
                break
            pass_current(session)

        self.assertEqual("graveyard", source.zone)
        self.assertNotIn("+1/+1", source.counters)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "source-combat-growth-lethal"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_combat_growth_shares_apnap_and_pins_source_incarnation(self):
        session = self.session(121067, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Attack Growth Trigger Fixture",
            ref="source-combat-growth-apnap",
            zone="battlefield",
        )
        observer = self.add_card(
            engine,
            seat="C",
            name="Typed Public Attack Trigger Fixture",
            ref="source-combat-growth-observer",
            zone="battlefield",
        )
        source_program = self.register_typed_event_trigger(engine, source)
        observer_program = self.register_typed_event_trigger(engine, observer)
        engine._issue_attackers()
        result = session.act(
            "pilot:A",
            {"a": "attack", "atk": {source.ref: "B"}},
        )
        self.assertTrue(result.ok, result.summary)
        applicable = [
            item
            for item in engine.state.stack
            if item.semantic_key in {source_program.key, observer_program.key}
        ]
        self.assertEqual(["A", "C"], [item.controller for item in applicable])
        self.assertEqual(
            [source_program.key, observer_program.key],
            [item.semantic_key for item in applicable],
        )

        departure_session = self.session(121068)
        departure_engine = departure_session.engine
        departure_engine.state.active_player = "A"
        departure_engine.state.phase_index = 5
        departure_engine.state.phase = "combat"
        departure_engine.state.step = "declare_attackers"
        departure_engine.state.combat = CombatState()
        departed = self.add_card(
            departure_engine,
            seat="A",
            name="Generic Attack Growth Trigger Fixture",
            ref="source-combat-growth-departure",
            zone="battlefield",
        )
        departed_program = self.register_typed_event_trigger(
            departure_engine,
            departed,
        )
        departure_engine._issue_attackers()
        departure_result = departure_session.act(
            "pilot:A",
            {"a": "attack", "atk": {departed.ref: "B"}},
        )
        self.assertTrue(departure_result.ok, departure_result.summary)
        self.assertTrue(
            any(
                item.semantic_key == departed_program.key
                for item in departure_engine.state.stack
            )
        )
        departure_engine.move_card(
            departed.object_id,
            "graveyard",
            reason="source combat growth incarnation witness",
        )
        self.resolve_top(departure_engine)
        self.assertEqual("graveyard", departed.zone)
        self.assertEqual([], departure_engine.state.continuous_effects)

    def test_land_entry_counter_trigger_uses_normalized_event(self):
        session = self.session(120002)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Landfall Counter Trigger Fixture",
            ref="landfall-counter-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)
        land = self.add_card(
            engine,
            seat="A",
            name="Forest",
            ref="landfall-entering-land",
            zone="hand",
        )

        engine.move_card(
            land.object_id,
            "battlefield",
            reason="Fixed counter Landfall fixture",
            semantic_events=True,
        )
        engine._stabilize()

        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual("land.enter", engine.state.stack[-1].context["event"])
        self.assertEqual(land.ref, engine.state.stack[-1].context["card"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_zone_entry_counter_triggers_apply_typed_subject_relations(self):
        session = self.session(120014)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Artifact Entry Counter Trigger Fixture",
            ref="controlled-artifact-entry-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)
        opponent_artifact = self.add_card(
            engine,
            seat="B",
            name="Sol Ring",
            ref="opponent-entering-artifact",
            zone="hand",
        )
        engine.move_card(
            opponent_artifact.object_id,
            "battlefield",
            reason="opponent artifact entry",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        controlled_artifact = self.add_card(
            engine,
            seat="A",
            name="Sol Ring",
            ref="controlled-entering-artifact",
            zone="hand",
        )
        engine.move_card(
            controlled_artifact.object_id,
            "battlefield",
            reason="controlled artifact entry",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual(
            controlled_artifact.ref,
            engine.state.stack[-1].context["card"],
        )
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("charge"))

        other_session = self.session(120015)
        other_engine = other_session.engine
        other_source = self.add_card(
            other_engine,
            seat="A",
            name="Other Artifact Entry Counter Trigger Fixture",
            ref="other-artifact-entry-source",
            zone="hand",
        )
        other_program = self.register_trigger(other_engine, other_source)
        other_engine.move_card(
            other_source.object_id,
            "battlefield",
            reason="source artifact entry",
            semantic_events=True,
        )
        other_engine._stabilize()
        self.assertFalse(other_engine.state.stack)

        unrelated = self.add_card(
            other_engine,
            seat="B",
            name="Sol Ring",
            ref="unrelated-entering-artifact",
            zone="hand",
        )
        other_engine.move_card(
            unrelated.object_id,
            "battlefield",
            reason="another artifact entry",
            semantic_events=True,
        )
        other_engine._stabilize()
        self.assertEqual(
            other_program.key,
            other_engine.state.stack[-1].semantic_key,
        )
        self.resolve_top(other_engine)
        self.assertEqual(1, other_source.counters.get("charge"))

    def test_subtype_entry_trigger_filters_and_uses_replacement_owner(self):
        session = self.session(120028)
        engine = session.engine
        record = self.db.lookup("Champion of the Parish")
        program = next(
            value
            for value in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if value.provenance.get("template_id")
            == "fixed-counter-subtype-entry-trigger-v1"
        )
        engine.semantics.put(program)
        source = self.add_card(
            engine,
            seat="A",
            name="Champion of the Parish",
            ref="subtype-entry-source",
            zone="battlefield",
        )
        vorinclex = self.add_card(
            engine,
            seat="A",
            name="Vorinclex, Monstrous Raider",
            ref="subtype-entry-vorinclex",
            zone="battlefield",
        )
        register_generated_programs(
            self.db,
            engine.semantics,
            (self.db.lookup("Vorinclex, Monstrous Raider"),),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )

        opponent_human = self.add_card(
            engine,
            seat="B",
            name="Mishra, Eminent One",
            ref="opponent-entering-human",
            zone="hand",
        )
        engine.move_card(
            opponent_human.object_id,
            "battlefield",
            reason="opponent Human subtype near miss",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        controlled_nonhuman = self.add_card(
            engine,
            seat="A",
            name="Sol Ring",
            ref="controlled-entering-nonhuman",
            zone="hand",
        )
        engine.move_card(
            controlled_nonhuman.object_id,
            "battlefield",
            reason="controlled subtype near miss",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        controlled_human = self.add_card(
            engine,
            seat="A",
            name="Mishra, Eminent One",
            ref="controlled-entering-human",
            zone="hand",
        )
        engine.move_card(
            controlled_human.object_id,
            "battlefield",
            reason="controlled Human subtype match",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertIn("human", engine.state.stack[-1].context["subtypes"])
        self.resolve_top(engine)

        self.assertEqual(2, source.counters.get("+1/+1"))
        replacement_event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "replacement.apply"
        )
        self.assertEqual(vorinclex.ref, replacement_event.details["source"])

    def test_creature_death_counter_trigger_uses_lki_subject_filters(self):
        session = self.session(120016)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Creature Death Counter Trigger Fixture",
            ref="death-counter-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)

        controlled_token = self.add_card(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="controlled-death-token",
            zone="battlefield",
            is_token=True,
        )
        engine.move_card(
            controlled_token.object_id,
            "graveyard",
            reason="controlled token death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        opponent_creature = self.add_card(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="opponent-death-creature",
            zone="battlefield",
        )
        engine.move_card(
            opponent_creature.object_id,
            "graveyard",
            reason="opponent creature death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        controlled_creature = self.add_card(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="controlled-death-creature",
            zone="battlefield",
        )
        previous_identity = controlled_creature.logical_object_id
        engine.move_card(
            controlled_creature.object_id,
            "graveyard",
            reason="controlled creature death",
            semantic_events=True,
        )
        engine._stabilize()
        item = engine.state.stack[-1]
        self.assertEqual(program.key, item.semantic_key)
        self.assertEqual("creature.dies", item.context["event"])
        self.assertEqual("A", item.context["previous_controller"])
        self.assertEqual(previous_identity, item.context["card_object_identity"])
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

        engine.move_card(
            source.object_id,
            "graveyard",
            reason="counter source death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

    def test_opponent_death_counter_trigger_uses_previous_controller(self):
        session = self.session(120017, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Opponent Death Counter Trigger Fixture",
            ref="opponent-death-counter-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)
        controlled = self.add_card(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="same-controller-death-creature",
            zone="battlefield",
        )
        engine.move_card(
            controlled.object_id,
            "graveyard",
            reason="same-controller creature death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertFalse(engine.state.stack)

        opponent = self.add_card(
            engine,
            seat="D",
            name="Scute Swarm",
            ref="different-controller-death-creature",
            zone="battlefield",
        )
        engine.move_card(
            opponent.object_id,
            "graveyard",
            reason="opponent creature death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual(
            "D",
            engine.state.stack[-1].context["previous_controller"],
        )
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

    def test_any_death_trigger_observes_opponents_and_its_own_lki(self):
        session = self.session(120019, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Any Creature Death Counter Trigger Fixture",
            ref="any-death-counter-source",
            zone="battlefield",
        )
        program = self.register_trigger(engine, source)
        opponent = self.add_card(
            engine,
            seat="D",
            name="Scute Swarm",
            ref="any-death-opponent-creature",
            zone="battlefield",
        )
        engine.move_card(
            opponent.object_id,
            "graveyard",
            reason="any-controller creature death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual(
            "D",
            engine.state.stack[-1].context["previous_controller"],
        )
        self.resolve_top(engine)
        self.assertEqual(1, source.counters.get("+1/+1"))

        previous_identity = source.logical_object_id
        engine.move_card(
            source.object_id,
            "graveyard",
            reason="source creature death",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual(
            previous_identity,
            engine.state.stack[-1].context["card_object_identity"],
        )
        self.assertEqual(
            "battlefield",
            engine.state.stack[-1].context["source_zone"],
        )

    def test_death_counter_replacement_is_private_and_replays_exactly(self):
        session = self.session(120018, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Creature Death Counter Trigger Fixture",
            ref="private-death-counter-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doubling Season",
            ref="private-death-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doc Samson, Super Psychiatrist",
            ref="private-death-addition",
            zone="battlefield",
        )
        self.register_trigger(engine, source)
        departed = self.add_card(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="private-death-creature",
            zone="battlefield",
        )

        engine.move_card(
            departed.object_id,
            "graveyard",
            reason="private replacement death",
            semantic_events=True,
        )
        engine._stabilize()
        self.resolve_top(engine)

        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        projected = projector._decision("pilot:C")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.finish_replacements(session, "C")

        self.assertIn(source.counters.get("+1/+1"), {3, 4})
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "death-counter-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_scheduled_counter_trigger_suspends_for_quantity_replacement(self):
        session = self.session(120003)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Scheduled Counter Trigger Fixture",
            ref="scheduled-replacement-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doubling Season",
            ref="scheduled-doubling-season",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="A",
            name="Doc Samson, Super Psychiatrist",
            ref="scheduled-doc-samson",
            zone="battlefield",
        )
        self.register_trigger(engine, source)

        engine._dispatch_semantic_event(
            "step.begin",
            self.step_context(player="A"),
        )
        engine._stabilize()
        self.resolve_top(engine)

        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertFalse(source.counters)
        self.finish_replacements(session, "A")
        self.assertIn(source.counters.get("charge"), {5, 6})

    def test_optional_counter_trigger_choice_composes_with_replacement_and_replay(
        self,
    ):
        session = self.session(120007, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Optional Scheduled Counter Trigger Fixture",
            ref="optional-counter-trigger-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doubling Season",
            ref="optional-trigger-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doc Samson, Super Psychiatrist",
            ref="optional-trigger-addition",
            zone="battlefield",
        )
        self.register_trigger(engine, source)

        def begin_choice() -> None:
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine.state.priority_passes = []
            engine._dispatch_semantic_event(
                "step.begin",
                self.step_context(player="C"),
            )
            engine._stabilize()
            self.resolve_top(engine)
            self.assertEqual(
                "semantic.choice",
                engine.state.pending_decision.kind,
            )

        begin_choice()
        declined = session.act(
            "pilot:C",
            {"action_id": "choose", "choice": "decline"},
        )
        self.assertTrue(declined.ok, declined.summary)
        self.assertFalse(source.counters)

        begin_choice()
        projector = StateProjector(self.db, engine.state)
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        projected = projector._decision("pilot:C")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:C",
            {"action_id": "choose", "choice": "put"},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(
            "replacement.order",
            engine.state.pending_decision.kind,
        )
        self.assertFalse(source.counters)
        self.finish_replacements(session, "C")
        self.assertIn(source.counters.get("charge"), {5, 6})

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "optional-counter-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_multiple_scheduled_counter_triggers_use_one_apnap_batch(self):
        session = self.session(120004, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source_a = self.add_card(
            engine,
            seat="A",
            name="Each Upkeep Counter Trigger Fixture",
            ref="apnap-counter-source-a",
            zone="battlefield",
        )
        source_c = self.add_card(
            engine,
            seat="C",
            name="Each Upkeep Counter Trigger Fixture",
            ref="apnap-counter-source-c",
            zone="battlefield",
        )
        self.register_trigger(engine, source_a)
        self.register_trigger(engine, source_c)

        items = collect_trigger_items(
            engine,
            "step.begin",
            self.step_context(player="A"),
        )
        self.assertEqual({"A", "C"}, {item.controller for item in items})
        enqueue_trigger_batch(engine, items)
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        self.assertEqual(
            ["A", "B", "C", "D"],
            list(engine.state.pending_trigger_batches[0].apnap_order),
        )

        engine._stabilize()

        self.assertEqual(
            ["A", "C"],
            [item.controller for item in engine.state.stack[-2:]],
        )
        self.assertEqual(
            {source_a.object_id, source_c.object_id},
            {item.source_object_id for item in engine.state.stack[-2:]},
        )

    def test_four_player_counter_trigger_choice_is_private_and_replays_exactly(
        self,
    ):
        session = self.session(120005, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            name="Scheduled Counter Trigger Fixture",
            ref="private-counter-trigger-source",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doubling Season",
            ref="private-trigger-doubling",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Doc Samson, Super Psychiatrist",
            ref="private-trigger-addition",
            zone="battlefield",
        )
        self.register_trigger(engine, source)

        engine._dispatch_semantic_event(
            "step.begin",
            self.step_context(player="C"),
        )
        engine._stabilize()
        self.resolve_top(engine)
        projector = StateProjector(self.db, engine.state)
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        projected = projector._decision("pilot:C")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.finish_replacements(session, "C")

        self.assertIn(source.counters.get("charge"), {5, 6})
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-counter-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_stale_counter_target_rolls_back_trigger_resolution(self):
        session = self.session(120006)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Targeted Scheduled Counter Trigger Fixture",
            ref="targeted-counter-trigger-source",
            zone="battlefield",
        )
        target = self.add_card(
            engine,
            seat="A",
            name="Scute Swarm",
            ref="stale-counter-trigger-target",
            zone="battlefield",
        )
        self.register_trigger(engine, source)

        engine._dispatch_semantic_event(
            "step.begin",
            self.step_context(player="A", step="beginning_combat"),
        )
        engine._stabilize()
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        selected = session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        target = engine.state.cards[target.object_id]
        engine.move_card(target.object_id, "graveyard", log=False)
        counter_snapshot = {
            object_id: dict(card.counters)
            for object_id, card in engine.state.cards.items()
        }

        self.resolve_top(engine)

        self.assertEqual("graveyard", target.zone)
        self.assertEqual(
            counter_snapshot,
            {
                object_id: dict(card.counters)
                for object_id, card in engine.state.cards.items()
            },
        )

    def test_fixed_typed_event_effect_trigger_resolves_targeted_body_and_replays(
        self,
    ):
        session = self.session(121001, players=4)
        engine = session.engine
        engine.state.active_player = "C"
        source = self.add_card(
            engine,
            seat="C",
            name="Typed Scheduled Target Trigger Fixture",
            ref="typed-target-trigger-source",
            zone="battlefield",
        )
        target = self.add_card(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="typed-target-trigger-target",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)

        engine._dispatch_semantic_event(
            "step.begin",
            self.step_context(player="C", step="beginning_combat"),
        )
        engine._stabilize()

        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.assertEqual("semantic.target", engine.state.pending_decision.kind)
        projector = StateProjector(self.db, engine.state)
        for seat in ("A", "B", "D"):
            self.assertIsNone(projector._decision(f"pilot:{seat}"))
        projected = projector._decision("pilot:C")
        self.assertIsNotNone(projected)
        self.assertNotIn(source.object_id, json.dumps(projected, sort_keys=True))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        selected = session.act(
            "pilot:C",
            {"action_id": "choose", "targets": [target.ref]},
        )
        self.assertTrue(selected.ok, selected.summary)
        for seat in ("C", "D", "A", "B"):
            passed = session.act(
                f"pilot:{seat}",
                {"action_id": "pass"},
            )
            self.assertTrue(passed.ok, passed.summary)

        self.assertEqual(3, engine._numeric_stat(target.object_id, "power"))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-typed-event-trigger-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_creature_subtype_dispatches_death_and_graveyard_wordings(self):
        session = self.session(121013)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Subtype Graveyard Trigger Fixture",
            ref="subtype-wording-source",
            zone="battlefield",
        )
        dies = self.register_subtype_graveyard_trigger(
            engine,
            source,
            event="creature.dies",
            qualifier_field="card",
        )
        graveyard = self.register_subtype_graveyard_trigger(
            engine,
            source,
            event="permanent.graveyard",
            qualifier_field="card",
        )
        departed = self.add_card(
            engine,
            seat="B",
            name="Generic Creature Goblin Fixture",
            ref="creature-goblin-departure",
            zone="battlefield",
        )

        engine.move_card(
            departed.object_id,
            "graveyard",
            reason="creature Goblin departure",
            semantic_events=True,
        )

        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        pending = engine.state.pending_trigger_batches[0].items
        self.assertEqual(2, len(pending))
        self.assertEqual(
            {dies.key, graveyard.key},
            {item.source_ability_id for item in pending},
        )
        self.assertEqual(
            {"creature.dies", "permanent.graveyard"},
            {item.normalized_event_id for item in pending},
        )
        engine._stabilize()
        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        ordered = session.act(
            "pilot:A",
            {
                "action_id": "order",
                "triggers": [item.ref for item in pending],
            },
        )
        self.assertTrue(ordered.ok, ordered.summary)

        items = [
            item
            for item in engine.state.stack
            if item.semantic_key in {dies.key, graveyard.key}
        ]
        self.assertEqual(2, len(items))
        self.assertEqual(
            {"creature.dies", "permanent.graveyard"},
            {item.context["event"] for item in items},
        )
        self.assertTrue(
            all("goblin" in item.context["subtypes"] for item in items)
        )

    def test_noncreature_kindred_subtype_only_dispatches_graveyard_wording(self):
        session = self.session(121014)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Subtype Graveyard Trigger Fixture",
            ref="kindred-wording-source",
            zone="battlefield",
        )
        dies = self.register_subtype_graveyard_trigger(
            engine,
            source,
            event="creature.dies",
            qualifier_field="card",
        )
        graveyard = self.register_subtype_graveyard_trigger(
            engine,
            source,
            event="permanent.graveyard",
            qualifier_field="card",
        )
        departed = self.add_card(
            engine,
            seat="B",
            name="Generic Kindred Goblin Fixture",
            ref="kindred-goblin-departure",
            zone="battlefield",
        )

        engine.move_card(
            departed.object_id,
            "graveyard",
            reason="noncreature Kindred Goblin departure",
            semantic_events=True,
        )
        engine._stabilize()

        items = [
            item
            for item in engine.state.stack
            if item.semantic_key in {dies.key, graveyard.key}
        ]
        self.assertEqual(1, len(items))
        self.assertEqual(graveyard.key, items[0].semantic_key)
        self.assertEqual("permanent.graveyard", items[0].context["event"])
        self.assertIn("kindred", items[0].context["types"])
        self.assertNotIn("creature", items[0].context["types"])

    def test_subtype_graveyard_another_and_nontoken_filters_use_lki(self):
        for qualifier, is_token, depart_source, expected in (
            ("card", False, True, False),
            ("token", True, False, False),
            ("token", False, False, True),
        ):
            with self.subTest(
                qualifier=qualifier,
                is_token=is_token,
                depart_source=depart_source,
            ):
                session = self.session(
                    121015 + int(is_token) + int(depart_source)
                )
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Typed Subtype Graveyard Trigger Fixture",
                    ref="subtype-filter-source",
                    zone="battlefield",
                )
                program = self.register_subtype_graveyard_trigger(
                    engine,
                    source,
                    event="permanent.graveyard",
                    qualifier_field=qualifier,
                )
                departed = source
                if not depart_source:
                    departed = self.add_card(
                        engine,
                        seat="B",
                        name="Generic Creature Goblin Fixture",
                        ref="subtype-filter-departure",
                        zone="battlefield",
                        is_token=is_token,
                    )

                engine.move_card(
                    departed.object_id,
                    "graveyard",
                    reason="subtype predicate departure",
                    semantic_events=True,
                )
                engine._stabilize()

                matched = any(
                    item.semantic_key == program.key
                    for item in engine.state.stack
                )
                self.assertEqual(expected, matched)

    def test_subtype_graveyard_control_and_owner_filters_use_lki(self):
        session = self.session(121018)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Subtype Graveyard Trigger Fixture",
            ref="subtype-control-source",
            zone="battlefield",
        )
        controlled = self.register_subtype_graveyard_trigger(
            engine,
            source,
            event="permanent.graveyard",
            qualifier_field="previous_controller",
        )
        departed = self.add_card(
            engine,
            seat="B",
            name="Generic Creature Goblin Fixture",
            ref="control-changed-goblin",
            zone="battlefield",
        )
        engine.change_control(
            departed.object_id,
            "A",
            reason="focused control-change witness",
        )

        engine.move_card(
            departed.object_id,
            "graveyard",
            reason="controlled Goblin departure",
            semantic_events=True,
        )
        engine._stabilize()

        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == controlled.key
        )
        self.assertEqual("A", item.context["previous_controller"])
        self.assertEqual("B", item.context["owner"])
        self.assertEqual("B", departed.controller)

        for owner, controller, expected in (
            ("A", "B", True),
            ("B", "A", False),
        ):
            with self.subTest(owner=owner, controller=controller):
                session = self.session(121019 + (owner == "B"))
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Typed Subtype Graveyard Trigger Fixture",
                    ref="subtype-owner-source",
                    zone="battlefield",
                )
                owned = self.register_subtype_graveyard_trigger(
                    engine,
                    source,
                    event="permanent.graveyard",
                    qualifier_field="owner",
                )
                departed = self.add_card(
                    engine,
                    seat=owner,
                    name="Generic Creature Goblin Fixture",
                    ref="owner-filter-goblin",
                    zone="battlefield",
                )
                if controller != owner:
                    engine.change_control(
                        departed.object_id,
                        controller,
                        reason="focused ownership witness",
                    )

                engine.move_card(
                    departed.object_id,
                    "graveyard",
                    reason="owner-filtered Goblin departure",
                    semantic_events=True,
                )
                engine._stabilize()

                matched = any(
                    value.semantic_key == owned.key
                    for value in engine.state.stack
                )
                self.assertIn(
                    departed.object_id,
                    engine.state.players[owner].zones["graveyard"],
                )
                self.assertEqual(expected, matched)

    def test_subtype_graveyard_triggers_preserve_apnap_and_exact_replay(self):
        session = self.session(121021, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source_a = self.add_card(
            engine,
            seat="A",
            name="Typed Subtype Graveyard Trigger Fixture",
            ref="subtype-apnap-source-a",
            zone="battlefield",
        )
        self.add_card(
            engine,
            seat="C",
            name="Typed Subtype Graveyard Trigger Fixture",
            ref="subtype-apnap-source-c",
            zone="battlefield",
        )
        program = self.register_subtype_graveyard_trigger(
            engine,
            source_a,
            event="permanent.graveyard",
            qualifier_field="card",
        )
        departed = self.add_card(
            engine,
            seat="B",
            name="Generic Creature Goblin Fixture",
            ref="subtype-apnap-departure",
            zone="battlefield",
        )

        engine.move_card(
            departed.object_id,
            "graveyard",
            reason="APNAP subtype graveyard occurrence",
            semantic_events=True,
        )

        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(("A", "B", "C", "D"), batch.apnap_order)
        self.assertEqual(
            ["A", "C"],
            [item.controller for item in batch.items],
        )
        self.assertTrue(
            all(
                item.source_ability_id == program.key
                and item.normalized_event_id == "permanent.graveyard"
                for item in batch.items
            )
        )

        engine._stabilize()
        self.assertEqual(
            ["A", "C"],
            [item.controller for item in engine.state.stack[-2:]],
        )
        self.assertTrue(
            all(
                item.context["event"] == "permanent.graveyard"
                for item in engine.state.stack[-2:]
            )
        )

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "subtype-graveyard-apnap-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_fixed_typed_event_effect_triggers_share_four_player_apnap_batch(
        self,
    ):
        session = self.session(121002, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source_a = self.add_card(
            engine,
            seat="A",
            name="Typed Each Upkeep Life Trigger Fixture",
            ref="typed-apnap-source-a",
            zone="battlefield",
        )
        source_c = self.add_card(
            engine,
            seat="C",
            name="Typed Each Upkeep Life Trigger Fixture",
            ref="typed-apnap-source-c",
            zone="battlefield",
        )
        self.register_typed_event_trigger(engine, source_a)
        self.register_typed_event_trigger(engine, source_c)

        items = collect_trigger_items(
            engine,
            "step.begin",
            self.step_context(player="A"),
        )
        self.assertEqual({"A", "C"}, {item.controller for item in items})
        self.assertTrue(
            all(
                item.semantic_key in {
                    program.key
                    for program in engine.semantics.programs()
                    if program.provenance.get("template_id")
                    in FIXED_TYPED_EVENT_TEMPLATE_IDS
                }
                for item in items
            )
        )
        enqueue_trigger_batch(engine, items)
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        self.assertEqual(
            ["A", "B", "C", "D"],
            list(engine.state.pending_trigger_batches[0].apnap_order),
        )
        engine._stabilize()
        self.assertEqual(
            ["A", "C"],
            [item.controller for item in engine.state.stack[-2:]],
        )

    def test_source_zone_lifecycle_uses_last_known_controller_and_replays(
        self,
    ):
        session = self.session(121008, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="C",
            controller="B",
            name="Typed Artifact Graveyard Draw Trigger Fixture",
            ref="typed-source-graveyard",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        previous_identity = source.logical_object_id
        hand_before = len(engine.state.players["B"].zones["hand"])

        engine.move_card(
            source.object_id,
            "graveyard",
            reason="typed source graveyard occurrence",
            semantic_events=True,
        )
        engine._stabilize()

        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("B", item.controller)
        self.assertEqual("permanent.graveyard", item.context["event"])
        self.assertEqual(source.ref, item.context["card"])
        self.assertEqual(previous_identity, item.context["card_object_identity"])
        self.assertEqual("battlefield", item.context["source_zone"])
        self.assertEqual("C", source.controller)

        engine.state.priority_player = engine.state.active_player
        engine._issue_priority(engine.state.active_player)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["B"].zones["hand"]),
        )

        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-source-graveyard-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_source_damage_bindings_use_committed_damage_events(self):
        session = self.session(121009, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Combat Damage Draw Trigger Fixture",
            ref="typed-combat-damage-source",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)
        hand_before = len(engine.state.players["A"].zones["hand"])

        for target, combat, suffix in (("B", False, "noncombat"),):
            resolve_damage_batch(
                engine,
                (
                    damage_proposal(
                        engine,
                        proposal_id=f"typed-source-damage:{suffix}",
                        actor="A",
                        source_ref=source.ref,
                        target=target,
                        amount=1,
                        combat=combat,
                        reason="typed source damage negative witness",
                    ),
                ),
            )
            engine._stabilize()
            self.assertFalse(
                any(
                    item.semantic_key == program.key
                    for item in engine.state.stack
                )
            )

        resolve_damage_batch(
            engine,
            (
                damage_proposal(
                    engine,
                    proposal_id="typed-source-damage:combat-player",
                    actor="A",
                    source_ref=source.ref,
                    target="B",
                    amount=1,
                    combat=True,
                    reason="typed source combat damage witness",
                ),
            ),
        )
        engine._stabilize()
        item = next(
            value
            for value in engine.state.stack
            if value.semantic_key == program.key
        )
        self.assertEqual("damage.dealt", item.context["event"])
        self.assertEqual(source.ref, item.context["card"])
        self.assertEqual("B", item.context["target"])
        self.assertEqual("player", item.context["target_kind"])
        self.assertTrue(item.context["combat"])
        for seat in engine.active_seats:
            packet_text = json.dumps(
                session.packet(f"pilot:{seat}", full=True),
                sort_keys=True,
            )
            self.assertNotIn(source.object_id, packet_text)
            self.assertNotIn(source.logical_object_id, packet_text)

        engine.state.priority_player = engine.state.active_player
        engine._issue_priority(engine.state.active_player)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for _ in range(12):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-source-damage-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        opponent_session = self.session(121010, players=4)
        opponent_engine = opponent_session.engine
        opponent_source = self.add_card(
            opponent_engine,
            seat="C",
            name="Typed Opponent Damage Draw Trigger Fixture",
            ref="typed-opponent-damage-source",
            zone="battlefield",
        )
        opponent_program = self.register_typed_event_trigger(
            opponent_engine,
            opponent_source,
        )
        resolve_damage_batch(
            opponent_engine,
            (
                damage_proposal(
                    opponent_engine,
                    proposal_id="typed-opponent-damage:controller",
                    actor="C",
                    source_ref=opponent_source.ref,
                    target="C",
                    amount=1,
                    combat=False,
                    reason="typed opponent relation negative witness",
                ),
            ),
        )
        opponent_engine._stabilize()
        self.assertFalse(opponent_engine.state.stack)
        resolve_damage_batch(
            opponent_engine,
            (
                damage_proposal(
                    opponent_engine,
                    proposal_id="typed-opponent-damage:opponent",
                    actor="C",
                    source_ref=opponent_source.ref,
                    target="D",
                    amount=1,
                    combat=False,
                    reason="typed opponent relation witness",
                ),
            ),
        )
        opponent_engine._stabilize()
        self.assertEqual(
            opponent_program.key,
            opponent_engine.state.stack[-1].semantic_key,
        )

        recipient_session = self.session(121011, players=4)
        recipient_engine = recipient_session.engine
        recipient = self.add_card(
            recipient_engine,
            seat="B",
            name="Typed Dealt Damage Life Trigger Fixture",
            ref="typed-damage-recipient",
            zone="battlefield",
        )
        recipient_program = self.register_typed_event_trigger(
            recipient_engine,
            recipient,
        )
        damage_source = self.add_card(
            recipient_engine,
            seat="A",
            name="Typed Combat Damage Draw Trigger Fixture",
            ref="typed-recipient-damage-source",
            zone="battlefield",
        )
        life_before = recipient_engine.state.players["B"].life
        resolve_damage_batch(
            recipient_engine,
            (
                damage_proposal(
                    recipient_engine,
                    proposal_id="typed-recipient-damage:permanent",
                    actor="A",
                    source_ref=damage_source.ref,
                    target=recipient.ref,
                    amount=1,
                    combat=False,
                    reason="typed dealt-damage recipient witness",
                ),
            ),
        )
        recipient_engine._stabilize()
        self.assertEqual(
            recipient_program.key,
            recipient_engine.state.stack[-1].semantic_key,
        )
        self.resolve_top(recipient_engine)
        self.assertEqual(
            life_before + 1,
            recipient_engine.state.players["B"].life,
        )

    def test_source_damage_event_dispatch_mutant_is_killed(self):
        session = self.session(121012)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Typed Combat Damage Draw Trigger Fixture",
            ref="typed-damage-dispatch-mutant",
            zone="battlefield",
        )
        program = self.register_typed_event_trigger(engine, source)

        with patch.object(
            engine,
            "_dispatch_semantic_event",
            return_value=[],
        ):
            resolve_damage_batch(
                engine,
                (
                    damage_proposal(
                        engine,
                        proposal_id="typed-source-damage:dispatch-mutant",
                        actor="A",
                        source_ref=source.ref,
                        target="B",
                        amount=1,
                        combat=True,
                        reason="typed source damage dispatch mutation",
                    ),
                ),
            )
        engine._stabilize()
        self.assertFalse(
            any(
                item.semantic_key == program.key
                for item in engine.state.stack
            )
        )

    def test_entry_return_choice_uses_owner_hand_and_replays(self):
        session = self.session(121069, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Entry Land Return Fixture",
            ref="entry-return-source",
            zone="hand",
        )
        candidate = self.add_card(
            engine,
            seat="B",
            name="Forest",
            ref="entry-return-candidate",
            zone="battlefield",
        )
        engine.change_control(
            candidate.object_id,
            "A",
            reason="entry-return owner-hand witness",
        )
        program = self.register_typed_event_trigger(engine, source)

        engine.move_card(
            source.object_id,
            "battlefield",
            reason="entry-return source entered",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(program.key, engine.state.stack[-1].semantic_key)
        self.resolve_top(engine)
        self.assertEqual("choice.apnap", engine.state.pending_decision.kind)
        self.assertEqual(["A"], engine.state.pending_decision.actors)
        projected = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projected._decision("pilot:A"))
        for seat in "BCD":
            self.assertIsNone(projected._decision(f"pilot:{seat}"))
        self.assertNotIn(
            candidate.object_id,
            json.dumps(projected._decision("pilot:A"), sort_keys=True),
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {"action_id": "choose", "cards": [candidate.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", candidate.zone)
        self.assertEqual("B", candidate.owner)
        self.assertIn(candidate.object_id, engine.state.players["B"].zones["hand"])
        self.assertEqual("battlefield", source.zone)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "entry-return-choice"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_entry_return_choice_revalidates_and_rolls_back(self):
        session = self.session(121075)
        engine = session.engine
        source = self.add_card(
            engine,
            seat="A",
            name="Generic Entry Creature Return Fixture",
            ref="entry-return-stale-source",
            zone="hand",
        )
        candidate = self.add_card(
            engine,
            seat="A",
            name="Generic Entry Dragon Witness",
            ref="entry-return-stale-candidate",
            zone="battlefield",
        )
        self.register_typed_event_trigger(engine, source)
        engine.move_card(
            source.object_id,
            "battlefield",
            reason="entry return stale source entered",
            semantic_events=True,
        )
        engine._stabilize()
        self.resolve_top(engine)
        self.assertEqual("choice.apnap", engine.state.pending_decision.kind)
        engine.move_card(candidate.object_id, "graveyard", log=False)
        expected_hash = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {"action_id": "choose", "cards": [candidate.ref]},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(expected_hash, authoritative_state_hash(engine.state))
        self.assertEqual("graveyard", candidate.zone)
        self.assertEqual("battlefield", source.zone)

    def test_entry_return_unless_branch_and_ability_removal(self):
        for seed, selection, expected_source_zone, expected_candidate_zone in (
            (121070, "sacrifice", "graveyard", "battlefield"),
            (121071, "return", "battlefield", "hand"),
        ):
            with self.subTest(selection=selection):
                session = self.session(seed)
                engine = session.engine
                source = self.add_card(
                    engine,
                    seat="A",
                    name="Generic Entry Unless Return Fixture",
                    ref=f"entry-unless-source-{selection}",
                    zone="hand",
                )
                candidate = self.add_card(
                    engine,
                    seat="A",
                    name="Generic Entry Dragon Witness",
                    ref=f"entry-unless-candidate-{selection}",
                    zone="battlefield",
                )
                self.register_typed_event_trigger(engine, source)
                engine.move_card(
                    source.object_id,
                    "battlefield",
                    reason="entry unless return source entered",
                    semantic_events=True,
                )
                engine._stabilize()
                self.resolve_top(engine)
                chosen = session.act(
                    "pilot:A",
                    {"action_id": "choose", "choice": selection},
                )
                self.assertTrue(chosen.ok, chosen.summary)
                if selection == "return":
                    returned = session.act(
                        "pilot:A",
                        {"action_id": "choose", "cards": [candidate.ref]},
                    )
                    self.assertTrue(returned.ok, returned.summary)
                self.assertEqual(expected_source_zone, source.zone)
                self.assertEqual(expected_candidate_zone, candidate.zone)

        fallback_session = self.session(121072)
        fallback_engine = fallback_session.engine
        fallback = self.add_card(
            fallback_engine,
            seat="A",
            name="Generic Entry Unless Return Fixture",
            ref="entry-unless-no-payment",
            zone="hand",
        )
        self.register_typed_event_trigger(fallback_engine, fallback)
        fallback_engine.move_card(
            fallback.object_id,
            "battlefield",
            reason="entry unless unavailable return",
            semantic_events=True,
        )
        fallback_engine._stabilize()
        self.resolve_top(fallback_engine)
        fallback_choice = fallback_session.act(
            "pilot:A",
            {"action_id": "choose", "choice": "return"},
        )
        self.assertTrue(fallback_choice.ok, fallback_choice.summary)
        self.assertEqual("graveyard", fallback.zone)

        removed_session = self.session(121073)
        removed_engine = removed_session.engine
        removed = self.add_card(
            removed_engine,
            seat="A",
            name="Generic Entry Creature Return Fixture",
            ref="entry-return-removed",
            zone="hand",
        )
        self.register_typed_event_trigger(removed_engine, removed)
        commit_continuous_effect(
            removed_engine.state,
            ContinuousEffect(
                effect_id="fixture:remove-entry-return",
                source_id="fixture:remove-entry-return-owner",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=removed_engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(
                    zones=("battlefield",),
                    types_all=("creature",),
                ),
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=removed.object_id,
                        logical_object_id=(
                            f"{removed.object_id}@{removed.zone_change_counter + 1}"
                        ),
                    ),
                ),
            ),
        )
        removed_engine.move_card(
            removed.object_id,
            "battlefield",
            reason="entry return removed ability witness",
            semantic_events=True,
        )
        removed_engine._stabilize()
        self.assertFalse(removed_engine.state.stack)

    def test_external_entry_self_returns_batch_in_apnap_order(self):
        session = self.session(121074, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        controlled_source = self.add_card(
            engine,
            seat="A",
            name="Generic Controlled Subtype Entry Return Fixture",
            ref="entry-return-controlled-source",
            zone="battlefield",
        )
        other_source = self.add_card(
            engine,
            seat="B",
            name="Generic Other Entry Return Fixture",
            ref="entry-return-other-source",
            zone="battlefield",
        )
        controlled_program = self.register_typed_event_trigger(
            engine, controlled_source
        )
        other_program = self.register_typed_event_trigger(engine, other_source)
        dragon = self.add_card(
            engine,
            seat="A",
            name="Generic Entry Dragon Witness",
            ref="entry-return-dragon",
            zone="hand",
        )
        engine.move_card(
            dragon.object_id,
            "battlefield",
            reason="external entry return Dragon witness",
            semantic_events=True,
        )
        engine._stabilize()
        self.assertEqual(
            {controlled_program.key, other_program.key},
            {item.semantic_key for item in engine.state.stack},
        )
        self.assertEqual("B", engine.state.stack[-1].controller)
        self.resolve_top(engine)
        self.assertEqual("hand", other_source.zone)
        self.resolve_top(engine)
        self.assertEqual("hand", controlled_source.zone)
        self.assertEqual("battlefield", dragon.zone)


if __name__ == "__main__":
    unittest.main()
