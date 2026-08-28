from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


BASELINE = ROOT / "platform" / "continuous-effect-performance-baseline.json"
RUNTIME_SOURCE = ROOT / "quorune" / "card_programs" / "runtime.py"


def _descriptor() -> dict[str, Any]:
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


def _fixture(players: int, permanents: int, descriptors: int):
    seats = [chr(ord("A") + index) for index in range(players)]
    cards = {}
    player_rows = {}
    programs = {}
    for seat in seats:
        battlefield = []
        for index in range(permanents):
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

    class Semantics:
        def runtime_handler_programs_for_oracle(
            self, oracle_id, *, active_zone, event
        ):
            if active_zone != "battlefield" or event != "characteristics.evaluate":
                raise AssertionError("unexpected benchmark query")
            return programs.get(oracle_id, ())

    return (
        SimpleNamespace(
            turn_order=seats,
            players=player_rows,
            cards=cards,
        ),
        Semantics(),
    )


def _collection_scenario(
    name: str,
    *,
    players: int,
    permanents: int,
    descriptors: int,
    repetitions: int,
) -> dict[str, Any]:
    state, semantics = _fixture(players, permanents, descriptors)
    metrics = ContinuousEffectCollectionMetrics()
    start = perf_counter()
    effect_count = 0
    for _ in range(repetitions):
        effect_count = len(
            collect_card_program_continuous_effects(
                state,
                semantics,
                lambda _program: True,
                metrics=metrics,
            )
        )
    latency = (perf_counter() - start) * 1000
    return {
        "name": name,
        "metrics": metrics.to_dict(),
        "effect_count_per_query": effect_count,
        "semantic_assertions": {
            "linear_object_scans": (
                metrics.battlefield_objects_inspected
                == players * permanents * repetitions
            ),
            "one_lookup_per_inspected_object": (
                metrics.card_program_lookups
                == metrics.battlefield_objects_inspected
            ),
        },
        "observed_latency_ms": round(latency, 3),
    }


def _layer_scenario() -> dict[str, Any]:
    effects = [
        ContinuousEffect(
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
        ),
        ContinuousEffect(
            effect_id="anthem",
            source_id="anthem-source",
            layer=Layer.POWER_TOUGHNESS,
            sublayer="7c",
            timestamp=1,
            operations=(
                ContinuousOperation("modify_power_toughness", [1, 1]),
            ),
            applies=ObjectQuerySpec(
                controller="A",
                subtypes_all=("thopter",),
            ),
        ),
    ]
    start = perf_counter()
    result = evaluate_continuous_effects(
        CharacteristicState(
            name="Benchmark Creature",
            controller="A",
            card_types={"Creature"},
            subtypes={"Goblin"},
            power=1,
            toughness=1,
        ),
        effects,
    )
    latency = (perf_counter() - start) * 1000
    return {
        "name": "earlier_layer_subtype_change",
        "metrics": {},
        "effect_count_per_query": 2,
        "semantic_assertions": {
            "ordered_type_before_anthem": result.applied_effects
            == ("type-change", "anthem"),
            "anthem_applied": result.characteristics["power"] == 2,
        },
        "observed_latency_ms": round(latency, 3),
    }


def run_benchmark() -> list[dict[str, Any]]:
    return [
        _collection_scenario(
            "empty_battlefield",
            players=4,
            permanents=0,
            descriptors=1,
            repetitions=1,
        ),
        _collection_scenario(
            "four_players_ten_permanents",
            players=4,
            permanents=10,
            descriptors=1,
            repetitions=1,
        ),
        _collection_scenario(
            "multiple_component_sources",
            players=4,
            permanents=10,
            descriptors=2,
            repetitions=1,
        ),
        _collection_scenario(
            "repeated_characteristic_queries",
            players=4,
            permanents=10,
            descriptors=2,
            repetitions=25,
        ),
        _layer_scenario(),
    ]


def _source_hash() -> str:
    return hashlib.sha256(RUNTIME_SOURCE.read_bytes()).hexdigest()


def _baseline_from(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source_sha256": _source_hash(),
        "latency_policy": (
            "Latency is recorded for observation only. CI gates deterministic "
            "work counters, semantic assertions, and source freshness."
        ),
        "scenarios": [
            {
                "name": row["name"],
                "expected_metrics": row["metrics"],
                "maximum_metrics": row["metrics"],
                "effect_count_per_query": row["effect_count_per_query"],
                "semantic_assertions": row["semantic_assertions"],
                "recorded_latency_ms": row["observed_latency_ms"],
            }
            for row in results
        ],
    }


def _check(results: list[dict[str, Any]]) -> list[str]:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    failures = []
    if baseline.get("source_sha256") != _source_hash():
        failures.append("runtime source fingerprint changed")
    expected = {row["name"]: row for row in baseline["scenarios"]}
    for result in results:
        row = expected.get(result["name"])
        if row is None:
            failures.append(f"missing scenario {result['name']}")
            continue
        if result["metrics"] != row["expected_metrics"]:
            failures.append(f"structural count changed: {result['name']}")
        for key, maximum in row["maximum_metrics"].items():
            if result["metrics"].get(key, 0) > int(maximum):
                failures.append(f"work budget exceeded: {result['name']}:{key}")
        if result["effect_count_per_query"] != row["effect_count_per_query"]:
            failures.append(f"effect count changed: {result['name']}")
        if not all(result["semantic_assertions"].values()):
            failures.append(f"semantic assertion failed: {result['name']}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    results = run_benchmark()
    if args.write:
        BASELINE.write_text(
            json.dumps(_baseline_from(results), indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return 0
    failures = _check(results)
    print(json.dumps({"failures": failures, "observed": results}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
