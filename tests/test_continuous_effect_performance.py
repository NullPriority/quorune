from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest import mock

from quorune.card_programs.runtime import (
    ContinuousEffectCollectionMetrics,
    collect_card_program_continuous_effects,
)
from quorune.continuous_effects import (
    CharacteristicState,
    ContinuousEffect,
    ContinuousOperation,
    Layer,
    evaluate_continuous_effects,
)
from quorune.object_predicate import ObjectQuerySpec
from quorune.model import CardInstance
from scripts.benchmark_continuous_effects import _source_hash_bytes


def _descriptor() -> dict:
    return {
        "handler_id": "continuous.anthem.power_toughness.v1",
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "condition": {
            "target_controller": "source_controller",
            "target_subtypes_all": ["thopter"],
        },
        "modifier": {"power": 1, "toughness": 1},
    }


def _fixture(*, players: int, permanents_per_player: int, descriptors: int = 1):
    seats = [chr(ord("A") + index) for index in range(players)]
    cards = {}
    player_rows = {}
    programs = {}
    for seat in seats:
        battlefield = []
        for index in range(permanents_per_player):
            object_id = f"{seat}-{index}"
            oracle_id = f"oracle-{seat}-{index}"
            battlefield.append(object_id)
            cards[object_id] = CardInstance(
                object_id=object_id,
                ref=f"P-{seat}-{index}",
                oracle_id=oracle_id,
                printed_name="Performance fixture source",
                controller=seat,
                owner=seat,zone='battlefield',
                phased_out=False,
                zone_timestamp=index,
            )
            programs[oracle_id] = (
                SimpleNamespace(
                    key=f"program:{oracle_id}",
                    provenance={},
                    handlers=[_descriptor() for _ in range(descriptors)],
                ),
            )
        player_rows[seat] = SimpleNamespace(
            zones={"battlefield": battlefield,"graveyard":[],"exile":[]}
        )
    state = SimpleNamespace(
        turn_order=seats,
        players=player_rows,
        cards=cards,
    )

    class Semantics:
        def runtime_handler_programs_for_oracle(
            self, oracle_id, *, active_zone, event
        ):
            if active_zone != "battlefield" or event != "characteristics.evaluate":
                raise AssertionError("unexpected collection query")
            return programs.get(oracle_id, ())

    return state, Semantics()


class ContinuousEffectPerformanceTests(unittest.TestCase):
    def test_runtime_source_fingerprint_is_line_ending_stable(self):
        self.assertEqual(
            _source_hash_bytes(b"first\nsecond\n"),
            _source_hash_bytes(b"first\r\nsecond\r\n"),
        )

    def test_empty_battlefield_has_zero_scan_work(self):
        state, semantics = _fixture(players=4, permanents_per_player=0)
        metrics = ContinuousEffectCollectionMetrics()
        effects = collect_card_program_continuous_effects(
            state, semantics, lambda _program: True, metrics=metrics
        )
        self.assertEqual((), effects)
        self.assertEqual(
            {
                "collection_calls": 1,
                "battlefield_objects_inspected": 0,
                "card_program_lookups": 0,
                "descriptors_inspected": 0,
                "effects_produced": 0,
            },
            metrics.to_dict(),
        )

    def test_four_by_ten_collection_work_is_linear_and_bounded(self):
        state, semantics = _fixture(players=4, permanents_per_player=10)
        metrics = ContinuousEffectCollectionMetrics()
        effects = collect_card_program_continuous_effects(
            state, semantics, lambda _program: True, metrics=metrics
        )
        self.assertEqual(40, len(effects))
        self.assertEqual(40, metrics.battlefield_objects_inspected)
        self.assertEqual(40, metrics.card_program_lookups)
        self.assertEqual(40, metrics.descriptors_inspected)
        self.assertEqual(40, metrics.effects_produced)

    def test_multiple_components_and_repeated_queries_have_exact_counts(self):
        state, semantics = _fixture(
            players=4, permanents_per_player=10, descriptors=2
        )
        metrics = ContinuousEffectCollectionMetrics()
        for _ in range(25):
            effects = collect_card_program_continuous_effects(
                state, semantics, lambda _program: True, metrics=metrics
            )
            self.assertEqual(80, len(effects))
        self.assertEqual(25, metrics.collection_calls)
        self.assertEqual(1_000, metrics.battlefield_objects_inspected)
        self.assertEqual(1_000, metrics.card_program_lookups)
        self.assertEqual(2_000, metrics.descriptors_inspected)
        self.assertEqual(2_000, metrics.effects_produced)

    def test_earlier_layer_subtype_change_enables_later_anthem(self):
        type_change = ContinuousEffect(
            effect_id="type-change",
            source_id="type-source",
            layer=Layer.TYPE,
            sublayer="4",
            timestamp=2,
            operations=(
                ContinuousOperation(
                    "add_types", ["Thopter"], field="subtypes"
                ),
            ),
        )
        anthem = ContinuousEffect(
            effect_id="anthem",
            source_id="anthem-source",
            layer=Layer.POWER_TOUGHNESS,
            sublayer="7c",
            timestamp=1,
            operations=(
                ContinuousOperation("modify_power_toughness", [1, 1]),
            ),
            applies=ObjectQuerySpec(
                controller="A", subtypes_all=("thopter",)
            ),
        )
        result = evaluate_continuous_effects(
            CharacteristicState(
                name="Benchmark Creature",
                controller="A",
                card_types={"Creature"},
                subtypes={"Goblin"},
                power=1,
                toughness=1,
            ),
            [anthem, type_change],
        )
        self.assertEqual(("type-change", "anthem"), result.applied_effects)
        self.assertEqual(2, result.characteristics["power"])


class StaticComponentPresenceCollectionTests(unittest.TestCase):
    def fixture(self):
        from quorune.compiler.continuous_templates import fixed_public_state_characteristics_handler

        compiled = fixed_public_state_characteristics_handler(
            "As long as you control three or more creatures, creatures you control get +1/+0 and have vigilance.",
            source_name="Source",
        )
        self.assertIsNotNone(compiled)
        descriptor = compiled[1]
        state, semantics = _fixture(players=2, permanents_per_player=1)
        for player in state.players.values():
            player.zones['hand'] = []
            player.life = 40
            player.in_game = True
        programs = []
        for source in state.cards.values():
            source.counters = {}
            program = semantics.runtime_handler_programs_for_oracle(
                source.oracle_id, active_zone='battlefield', event='characteristics.evaluate')[0]
            program.handlers = [deepcopy(descriptor)]
            programs.append(program)
        return state, semantics, programs

    @staticmethod
    def presence_effects(effects):
        return tuple(effect for effect in effects if any(operation.op in {
            'add_ability_fragment', 'remove_ability_fragment', 'remove_all_abilities',
        } for operation in effect.operations))

    def test_presence_candidates_preserve_result_and_avoid_irrelevant_live_queries(self):
        state, semantics, _programs = self.fixture()
        quantity = mock.Mock(return_value=3)
        full = collect_card_program_continuous_effects(
            state, semantics, lambda _program: True, quantity_resolver=quantity)
        self.assertEqual(4, len(full))
        self.assertEqual({Layer.ABILITY, Layer.POWER_TOUGHNESS}, {effect.layer for effect in full})
        self.assertEqual(2, quantity.call_count)
        quantity.reset_mock()
        candidates = collect_card_program_continuous_effects(
            state, semantics, lambda _program: True, maximum_layer=Layer.ABILITY,
            quantity_resolver=quantity, static_component_presence_only=True)
        self.assertEqual(self.presence_effects(full), self.presence_effects(candidates))
        quantity.assert_not_called()

    def test_presence_candidates_retain_fragments_and_recheck_current_descriptor(self):
        from quorune.semantic_runtime.conditional_continuous import FixedPublicStateGrantedAbilityHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        from test_continuous_effect_components import ability_grant_descriptor

        state, semantics, programs = self.fixture()
        handler = FixedPublicStateGrantedAbilityHandler()
        fragment = ability_grant_descriptor()['modifier']['add_ability_fragments'][0]
        originals = [deepcopy(program.handlers[0]) for program in programs]
        for program in programs:
            descriptor = program.handlers[0]
            descriptor.update(handler_id=handler.handler_id, schema_version=handler.schema_version)
            descriptor['modifier']['add_ability_fragments'] = [deepcopy(fragment)]
        for count, expected in ((3, 2), (0, 0)):
            with self.subTest(current_count=count):
                quantity = mock.Mock(return_value=count)
                full = collect_card_program_continuous_effects(
                    state, semantics, lambda _program: True, quantity_resolver=quantity)
                candidates = collect_card_program_continuous_effects(
                    state, semantics, lambda _program: True, maximum_layer=Layer.ABILITY,
                    quantity_resolver=quantity, static_component_presence_only=True)
                self.assertEqual(expected, len(self.presence_effects(candidates)))
                self.assertEqual(self.presence_effects(full), self.presence_effects(candidates))
                self.assertEqual(4, quantity.call_count)
        programs[0].handlers[0]['modifier']['add_ability_fragments'] = []
        with self.assertRaisesRegex(SemanticNodeError, 'exactly one'):
            collect_card_program_continuous_effects(
                state, semantics, lambda _program: True, quantity_resolver=lambda *_args: 0,
                static_component_presence_only=True)
        for program, descriptor in zip(programs, originals):
            program.handlers = [descriptor]
        programs[0].handlers[0]['modifier']['power'] = '1'
        with self.assertRaisesRegex(SemanticNodeError, 'integers'):
            collect_card_program_continuous_effects(
                state, semantics, lambda _program: True, static_component_presence_only=True)

    def test_presence_candidates_do_not_repeat_retained_fragment_validation(self):
        from quorune.semantic_runtime.conditional_continuous import (
            FixedPublicStateCharacteristicsHandler, FixedPublicStateGrantedAbilityHandler)
        from test_continuous_effect_components import ability_grant_descriptor

        state, semantics, programs = self.fixture()
        handler = FixedPublicStateGrantedAbilityHandler()
        fragment = ability_grant_descriptor()['modifier']['add_ability_fragments'][0]
        for program in programs:
            descriptor = program.handlers[0]
            descriptor.update(handler_id=handler.handler_id, schema_version=handler.schema_version)
            descriptor['modifier']['add_ability_fragments'] = [deepcopy(fragment)]
        collect = lambda **kwargs: collect_card_program_continuous_effects(
            state, semantics, lambda _program: True, maximum_layer=Layer.ABILITY,
            quantity_resolver=lambda *_args: 3, **kwargs)
        collect()
        validations = []
        original = FixedPublicStateCharacteristicsHandler.validate

        def observe(current, descriptor):
            validations.append(current.handler_id)
            return original(current, descriptor)

        with mock.patch.object(FixedPublicStateCharacteristicsHandler, 'validate', observe):
            full = collect()
            full_validations = list(validations)
            self.assertEqual(len(programs), len(full_validations))
            validations.clear()
            candidates = collect(static_component_presence_only=True)
            self.assertEqual(full, candidates)
            self.assertEqual(full_validations, validations)

    def test_presence_filter_retains_unclassified_or_overridden_handlers(self):
        from quorune.semantic_runtime.conditional_continuous import FixedPublicStateCharacteristicsHandler
        from quorune.semantic_runtime.continuous_components import (
            ContinuousEffectComponentRegistry, FixedPowerToughnessAnthemHandler)

        class FutureHandler(FixedPublicStateCharacteristicsHandler):
            def lower(self, descriptor, context):
                self.validate(descriptor)
                return (ContinuousEffect(
                    effect_id=context.component_id, source_id=context.source_object_id,
                    layer=Layer.ABILITY, sublayer='6', timestamp=context.source_timestamp,
                    operations=(ContinuousOperation('remove_all_abilities'),)),)

        handler = FutureHandler(handler_id='fixture.future-presence')
        registry = ContinuousEffectComponentRegistry((handler, FixedPowerToughnessAnthemHandler())).freeze()
        self.assertTrue(registry.may_affect_static_components(_descriptor()))
        state, semantics, programs = self.fixture()
        for program in programs:
            program.handlers[0]['handler_id'] = handler.handler_id
        with mock.patch('quorune.card_programs.runtime.default_continuous_effect_component_registry', return_value=registry):
            full = collect_card_program_continuous_effects(state, semantics, lambda _program: True)
            candidates = collect_card_program_continuous_effects(
                state, semantics, lambda _program: True, static_component_presence_only=True)
        self.assertEqual(2, len(self.presence_effects(candidates)))
        self.assertEqual(full, candidates)


if __name__ == "__main__":
    unittest.main()
