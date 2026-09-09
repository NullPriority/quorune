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
                "permanent.enter",
                "fixed-typed-effect-constellation-entry-trigger-v1",
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

        targeted = self.compile(
            "When this creature enters, return up to one target creature you "
            "control to its owner's hand.",
            type_line="Creature — Test",
        )
        self.assertEqual("exact", targeted.status, targeted.material_residuals)
        targeted_node = targeted.faces[0].nodes[0]
        self.assertEqual("permanent.enter.self", targeted_node.event)
        self.assertEqual(
            "return-target-creature-you-v2-fixed-up-to-1-target-set-v1",
            targeted_node.template_id,
        )
        self.assertEqual(
            "return_permanent_targets_to_owner_hand",
            targeted_node.effects[0]["op"],
        )
        self.assertIn(
            "resolution.effect.fixed_homogeneous_target_set",
            targeted_node.capability_dependencies,
        )

        exclusions = (
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

    def test_entry_return_untapped_basic_land_subtypes_compile_exactly(self):
        for subtype in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
            with self.subTest(subtype=subtype):
                ir = self.compile(
                    "When this land enters, return an untapped "
                    f"{subtype} you control to its owner's hand.",
                    type_line="Land",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                effect = node.effects[0]
                self.assertEqual("choose_cards_apnap", effect["op"])
                self.assertEqual(["land"], effect["predicate"]["types_all"])
                self.assertEqual(
                    [subtype.casefold()],
                    effect["predicate"]["subtypes_all"],
                )
                self.assertFalse(effect["predicate"]["tapped"])

    def test_entry_return_counts_lower_as_many_and_exact_unless_shapes(self):
        for word, count, quality, pronoun in (
            ("a", 1, "creature", "its"),
            ("two", 2, "creatures", "their"),
            ("three", 3, "creatures", "their"),
        ):
            with self.subTest(count=count, payment="as-many-as-possible"):
                ordinary = self.compile(
                    "When this land enters, return "
                    f"{word} {quality} you control to {pronoun} owner's hand.",
                    type_line="Land",
                )
                self.assertEqual("exact", ordinary.status, ordinary.material_residuals)
                choice = ordinary.faces[0].nodes[0].effects[0]
                self.assertEqual("choose_cards_apnap", choice["op"])
                self.assertEqual(count, choice["count"])
                self.assertNotIn("require_full_count", choice)
                self.assertNotIn("fallback_effects", choice)

            with self.subTest(count=count, payment="exact-unless"):
                unless = self.compile(
                    "When this land enters, sacrifice it unless you return "
                    f"{word} {quality} you control to {pronoun} owner's hand.",
                    type_line="Land",
                )
                self.assertEqual("exact", unless.status, unless.material_residuals)
                option = unless.faces[0].nodes[0].effects[0]
                choice = option["then_by_choice"]["return"][0]
                self.assertEqual("choose_cards_apnap", choice["op"])
                self.assertEqual(count, choice["count"])
                self.assertTrue(choice["require_full_count"])
                fallback = [
                    {"op": "sacrifice_if_present", "card": "$source.zone_object"}
                ]
                self.assertEqual(fallback, choice["fallback_effects"])
                self.assertEqual(
                    fallback,
                    option["then_by_choice"]["sacrifice"],
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


if __name__ == "__main__":
    unittest.main()
