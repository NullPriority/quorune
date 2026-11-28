from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest import mock

from quorune.ability_fragments import StaticComponentSpec
from quorune.characteristic_evaluation_host import CharacteristicEvaluationHostMixin
from quorune.characteristic_read_frames import characteristic_read_frame
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
from quorune.model import CardInstance, PlayerState
from quorune.static_component_scope import StaticComponentApplicabilitySpec, StaticComponentScopeSpec
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
        player_rows[seat] = PlayerState(seat=seat, name=f"Performance {seat}")
        player_rows[seat].zones["battlefield"] = battlefield
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
    def test_ability_layer_preview_runs_only_for_potential_quantity_fragments(self):
        from quorune.ability_fragments import ability_fragment_to_dict
        from quorune.characteristic_evaluation import evaluate_card_characteristics
        from quorune.characteristic_fragments import (
            CharacteristicQuantityScope, CharacteristicQuantitySpec,
            PowerToughnessCalculation, QueryCharacteristicModifierSpec,
        )

        state, _ = _fixture(players=1, permanents_per_player=1)
        card = state.cards["A-0"]
        base = {"name": card.printed_name, "type_line": "Creature", "power": "2", "toughness": "2"}
        quantity = CharacteristicQuantitySpec(
            CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
        )
        fragment = ability_fragment_to_dict(QueryCharacteristicModifierSpec(
            quantity, PowerToughnessCalculation.FIXED_IF_THRESHOLD,
            power=0, toughness=0, minimum_count=2, add_abilities=("Flying",),
        ))
        count = mock.Mock(return_value=3)

        def effect(operation, layer, sublayer, *, timestamp=1):
            return ContinuousEffect(
                "quantity-fixture:" + operation.op, card.object_id, layer, sublayer, timestamp,
                (operation,),
            )

        cases = (
            (base, (effect(ContinuousOperation("add_ability", "Vigilance"), Layer.ABILITY, "6"),), False),
            ({**base, "ability_fragments": [fragment]}, (), True),
            (base, (effect(ContinuousOperation("copy_values", {"ability_fragments": [fragment]}), Layer.COPY, "1a"),), True),
            (base, (effect(ContinuousOperation("face_down", {"ability_fragments": [fragment]}), Layer.COPY, "1b"),), True),
            (base, (effect(ContinuousOperation("add_ability_fragment", fragment), Layer.ABILITY, "6"),), True),
            ({**base, "ability_fragments": [fragment]},
             (effect(ContinuousOperation("remove_all_abilities"), Layer.ABILITY, "6", timestamp=2),), False),
        )
        from quorune import characteristic_evaluation
        with mock.patch.object(
            characteristic_evaluation, "evaluate_continuous_effects",
            wraps=evaluate_continuous_effects,
        ) as evaluate:
            for index, (characteristics, effects, flying) in enumerate(cases):
                with self.subTest(case=index):
                    evaluate.reset_mock()
                    count.reset_mock()
                    data = evaluate_card_characteristics(
                        card, characteristics, runtime_effects=effects,
                        maximum_layer=Layer.ABILITY, query_count_resolver=count,
                    )
                    self.assertEqual(flying, "flying" in {keyword.casefold() for keyword in data["keywords"]})
                    self.assertEqual(int(flying), count.call_count)
                    self.assertEqual(1 if index == 0 else 2, evaluate.call_count)

    def test_render_keeps_the_canonical_layered_activation_snapshot(self):
        from quorune.abilities import ActivatedAbility
        from quorune.characteristic_evaluation import evaluate_card_characteristics

        state, _ = _fixture(players=1, permanents_per_player=1)
        card = state.cards["A-0"]
        base = {"name": card.printed_name, "type_line": "Basic Land — Island"}
        effect = ContinuousEffect(
            "activation-fixture", card.object_id, Layer.ABILITY, "6", 1,
            (ContinuousOperation("add_ability", "Vigilance"),),
        )
        with mock.patch.object(ActivatedAbility, "from_dict", wraps=ActivatedAbility.from_dict) as parse:
            first = evaluate_card_characteristics(card, base, runtime_effects=(effect,))
            ability = first["activated_abilities"][0]
            self.assertEqual(1, ability["fixed_mana_outputs"][0]["bundle"]["U"])
            unchanged = deepcopy(ability)
            ability["fixed_mana_outputs"][0]["bundle"]["U"] = 2
            second = evaluate_card_characteristics(card, base, runtime_effects=(effect,))
            parse.assert_not_called()
        self.assertEqual([unchanged], second["activated_abilities"])
        self.assertEqual(unchanged, ActivatedAbility.from_dict(unchanged).to_dict())

    def test_static_cast_rule_collection_is_scoped_to_the_active_read(self):
        from quorune.semantic_runtime.static_cast_rules import active_static_cast_rules

        state, _ = _fixture(players=2, permanents_per_player=1)
        source = state.cards["A-0"]
        host = SimpleNamespace(
            _semantic_event_sources=mock.Mock(return_value=(source,)),
            card_record=mock.Mock(return_value=None),
        )
        with characteristic_read_frame(host):
            self.assertEqual((), active_static_cast_rules(host))
            self.assertEqual((), active_static_cast_rules(host))
        host._semantic_event_sources.assert_called_once_with(zones={"battlefield"})
        host.card_record.assert_called_once_with(source)

        source.zone = "graveyard"
        with characteristic_read_frame(host):
            self.assertEqual((), active_static_cast_rules(host))
        self.assertEqual(2, host._semantic_event_sources.call_count)
        host.card_record.assert_called_once_with(source)

    def test_characteristic_collection_reuses_only_the_current_read_and_layer(self):
        state, semantics = _fixture(players=2, permanents_per_player=1)

        class Host(CharacteristicEvaluationHostMixin):
            _commander_identity = mock.Mock()
            _public_object_query_result = mock.Mock()
            _static_component_keys_at_layer = mock.Mock()
            _copied_static_source_fragments = mock.Mock(return_value=None)
            semantic_program_is_current_trusted = mock.Mock()

        host = Host()
        host.state = state
        host.semantics = semantics
        source = state.cards["A-0"]

        def collect(*_args, maximum_layer, **_kwargs):
            return () if source.phased_out or maximum_layer is not None else ("resolved",)

        def read(layer, enforce):
            return host._battlefield_program_characteristic_effects(
                maximum_layer=layer, enforce_static_components=enforce,
                keys={} if enforce else None,
            )

        with mock.patch(
            "quorune.characteristic_evaluation_host.collect_card_program_continuous_effects",
            side_effect=collect,
        ) as collector, mock.patch.object(
            host, "_effective_static_component_fragment_map", return_value={},
        ) as fragments:
            with characteristic_read_frame(host):
                self.assertEqual(("resolved",), read(None, True))
                self.assertEqual(("resolved",), read(None, True))
                self.assertEqual((), read(Layer.TYPE, True))
                self.assertEqual(("resolved",), read(None, False))
                self.assertEqual(3, collector.call_count)
                self.assertEqual(2, fragments.call_count)
            source.phased_out = True
            with characteristic_read_frame(host):
                self.assertEqual((), read(None, True))
                self.assertEqual(4, collector.call_count)
                self.assertEqual(3, fragments.call_count)

    def test_static_component_keys_are_read_only_and_fresh_in_a_new_frame(self):
        state, _ = _fixture(players=4, permanents_per_player=2)
        host = CharacteristicEvaluationHostMixin()
        host.state = state
        fragments = {object_id: () for object_id in state.cards}
        source = state.cards["A-0"]
        fragments[source.object_id] = (
            StaticComponentSpec("test:parent"),
            StaticComponentSpec("test:child"),
            StaticComponentScopeSpec(
                parent_semantic_key="test:parent",
                child_semantic_keys=("test:child",),
                keywords=(),
                applicability=StaticComponentApplicabilitySpec(
                    kind="controller_speed_equals", designation="controller_speed", minimum=4,
                ),
            ),
        )
        with mock.patch.object(host, "_effective_static_component_fragment_map", return_value=fragments) as collect:
            with characteristic_read_frame(host):
                keys = host._effective_static_component_key_map()
                self.assertEqual(("test:parent",), keys[source.object_id])
                self.assertEqual((), keys["B-0"])
                self.assertIs(keys, host._effective_static_component_key_map())
                with self.assertRaises(TypeError):
                    keys[source.object_id] = ()
                collect.assert_called_once_with()

            state.players[source.controller].stats["speed"] = 4
            with characteristic_read_frame(host):
                current = host._effective_static_component_key_map()
                self.assertEqual(("test:child", "test:parent"), current[source.object_id])
                self.assertEqual(("test:parent",), keys[source.object_id])

            source.phased_out = True
            del fragments[source.object_id]
            with characteristic_read_frame(host):
                self.assertNotIn(source.object_id, host._effective_static_component_key_map())
            self.assertEqual(3, collect.call_count)

    def test_static_component_read_projects_only_an_independent_typed_trigger_grant(self):
        from dataclasses import replace

        from quorune.ability_fragments import (
            GrantedTriggeredAbilitySpec, ability_fragment_to_dict, canonical_ability_fragments, granted_triggered_specs,
        )
        from quorune.characteristic_evaluation import evaluate_card_characteristics
        from quorune.continuous_effects import ContinuousEffectOrigin
        from quorune.static_component_scope import GRANTED_STATIC_CHARACTERISTIC_EVENT

        state, _ = _fixture(players=1, permanents_per_player=1)
        state.continuous_effects = ()
        source = state.cards["A-0"]
        trigger = GrantedTriggeredAbilitySpec("trigger:granted", "fixture:trigger",
            "permanent.enter.self", "Fixture granted trigger")
        printed = StaticComponentSpec("fixture:printed")
        copied = StaticComponentSpec("fixture:copied")
        granted = StaticComponentSpec(
            "fixture:granted", schema_version=3, event=GRANTED_STATIC_CHARACTERISTIC_EVENT,
        )
        base = {
            "name": source.printed_name,
            "type_line": "Creature",
            "ability_fragments": [ability_fragment_to_dict(printed)],
            "activated_abilities": [],
        }

        def add(fragment):
            return ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(fragment))

        grant = ContinuousEffect(
            "fixture:grant", source.object_id, Layer.ABILITY, "6", 1, (add(trigger),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=ObjectQuerySpec(zones=("battlefield",)),
        )
        copy_values = ContinuousEffect(
            "fixture:copy", source.object_id, Layer.COPY, "1a", 1,
            (ContinuousOperation("copy_values", {"ability_fragments": [ability_fragment_to_dict(copied)]}),),
        )
        remove = ContinuousEffect(
            "fixture:remove", source.object_id, Layer.ABILITY, "6", 2,
            (ContinuousOperation("remove_all_abilities"),),
        )

        class Host(CharacteristicEvaluationHostMixin):
            def __init__(self, effects, compatibility=False):
                self.state = state
                self.semantics = SimpleNamespace(
                    runtime_handler_compatibility_enabled=compatibility,
                    programs_for_oracle=lambda *_: (),
                    runtime_handler_programs_for_oracle=lambda *_, **__: (),
                )
                self._static_component_candidate_effects = lambda: effects
                self._attachment_public_state = lambda *_, **__: (False, False, False)
                self._apply_layered_characteristic_annotations = mock.Mock(
                    side_effect=lambda card, raw, runtime_effects, maximum_layer, **_: evaluate_card_characteristics(
                        card, raw, runtime_effects=runtime_effects, maximum_layer=maximum_layer,
                    )
                )

            def card_record(self, card):
                return SimpleNamespace(faces=())

            def _compiled_base_characteristics(self, *_, **__):
                return base

        full = evaluate_card_characteristics(source, base, runtime_effects=(grant,), maximum_layer=Layer.ABILITY)
        self.assertEqual((trigger,), granted_triggered_specs(canonical_ability_fragments(full["ability_fragments"])))
        host = Host((grant,))
        self.assertEqual({source.object_id: (printed,)}, host._effective_static_component_fragment_map())
        host._apply_layered_characteristic_annotations.assert_not_called()

        cases = (
            ("disabling", (grant, remove), (), (), False),
            ("effects introduce a static occurrence", (replace(grant, operations=(add(trigger), add(granted))),),
             (), ("fixture:granted", "fixture:printed"), False),
            ("copy changes static identity", (grant, copy_values), (), ("fixture:copied",), False),
            ("explicit order", (replace(grant, depends_on=("fixture:earlier",)),), (), ("fixture:printed",), False),
            ("local resolution", (grant,), (remove,), (), False),
            ("historical compatibility", (grant,), (), ("fixture:printed",), True),
        )
        for label, effects, active, keys, compatibility in cases:
            with self.subTest(label=label), mock.patch(
                "quorune.characteristic_evaluation_host.active_resolution_effects", return_value=active,
            ):
                host = Host(effects, compatibility)
                self.assertEqual(keys, host._effective_static_component_key_map()[source.object_id])
                host._apply_layered_characteristic_annotations.assert_called_once()

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
