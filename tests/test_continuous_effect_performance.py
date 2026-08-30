from __future__ import annotations

from types import SimpleNamespace
import unittest

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
            cards[object_id] = SimpleNamespace(
                object_id=object_id,
                logical_object_id=f"logical-{object_id}",
                ref=f"P-{seat}-{index}",
                oracle_id=oracle_id,
                controller=seat,
                phased_out=False,
                zone_timestamp=index,
            )
            programs[oracle_id] = (
                SimpleNamespace(
                    key=f"program:{oracle_id}",
                    handlers=[_descriptor() for _ in range(descriptors)],
                ),
            )
        player_rows[seat] = SimpleNamespace(
            zones={"battlefield": battlefield}
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


if __name__ == "__main__":
    unittest.main()
