from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
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
    FIXED_TYPED_EVENT_EFFECT_TRIGGER_MECHANIC,
    FIXED_TYPED_EVENT_EFFECT_TRIGGER_TEMPLATE_IDS,
    OPTIONAL_COUNTER_PLACEMENT_OPERATION,
    OPTIONAL_FIXED_COUNTER_EVENT_TRIGGER_MECHANIC,
    FixedCounterTriggerBinding,
    FixedCounterTriggerEvent,
    FixedCounterZoneController,
    FixedCounterZoneSubject,
    fixed_counter_trigger_binding,
)
from quorune.compiler.target_effect_corpus_assurance import (
    TargetEffectCorpusCollector,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance
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
            replace(
                self.db.lookup("Scheduled Counter Trigger Fixture"),
                name="Compiler Fixture",
                oracle_text=text,
                type_line=type_line,
                keywords=(),
                faces=(),
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

    def test_fixed_typed_event_effect_trigger_variants_remain_material(self):
        cases = (
            "At the beginning of your upkeep, you may draw a card.",
            "At the beginning of your upkeep, if you have no cards in hand, "
            "draw a card.",
            "Whenever an opponent casts a spell, draw a card.",
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
            "Whenever an opponent casts a noncreature spell, put a +1/+1 counter on this creature.",
            "Whenever a land enters, put a +1/+1 counter on this creature.",
            "Whenever an opponent gains life, put a +1/+1 counter on this creature.",
            "Whenever an opponent draws a card, put a +1/+1 counter on this creature.",
            "Whenever you draw your third card each turn, put a +1/+1 counter on this creature.",
            "At the beginning of your upkeep, if you control a creature, put a charge counter on this artifact.",
            "At the beginning of your upkeep, put X charge counters on this artifact.",
            "At the beginning of your upkeep, you may put X charge counters on this artifact.",
            "At the beginning of your upkeep, you may put a charge counter on this artifact. If you do, draw a card.",
            "At the beginning of your upkeep, you may put a charge counter on this artifact, then gain 1 life.",
            "You may put a charge counter on this artifact.",
            "At the beginning of your upkeep, move a charge counter from this artifact onto target creature.",
            "At the beginning of your upkeep, remove a charge counter from this artifact.",
            "Whenever another Zombie you control dies, put a +1/+1 counter on this creature.",
            "Whenever one or more creatures die, put a +1/+1 counter on this creature.",
            "Whenever another creature you control enters or dies, put a +1/+1 counter on this creature.",
            "Whenever another creature you control leaves the battlefield, put a +1/+1 counter on this creature.",
            "Whenever another creature with a counter on it dies, put a +1/+1 counter on this creature.",
            "Whenever another Zombie you control dies, you may put a +1/+1 counter on this creature.",
            "Whenever another artifact dies, put a charge counter on this artifact.",
            "Whenever this artifact or another creature enters, put a charge counter on this artifact.",
            "Whenever another Human or Zombie you control enters, put a +1/+1 counter on this creature.",
            "Whenever another legendary Human you control enters, put a +1/+1 counter on this creature.",
            "Whenever another Human you control dies, put a +1/+1 counter on this creature.",
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
            "quorune.oracle_ir.fixed_counter_event_trigger_node",
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


if __name__ == "__main__":
    unittest.main()
