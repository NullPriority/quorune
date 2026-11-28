"""The exact announced price and target for a persistent artifact self-copy."""

from __future__ import annotations

from collections.abc import Mapping

from ..activation_x import ANNOUNCED_ACTIVATION_X_CAPABILITY
from ..compiler.direct_target import DirectPermanentTargetSpec
from ..replacement.immutable import thaw_value


ANNOUNCED_ARTIFACT_COPY_VERSION = "announced_artifact_copy_version"
ANNOUNCED_ARTIFACT_COPY_TEMPLATE = "announced-artifact-self-copy-v1"
ANNOUNCED_ARTIFACT_COPY_CAPABILITY = "continuous.resolution.announced_artifact_self_copy"
ANNOUNCED_ARTIFACT_COPY_DEPENDENCIES = (
    ANNOUNCED_ARTIFACT_COPY_CAPABILITY,
    ANNOUNCED_ACTIVATION_X_CAPABILITY,
    "target.revalidate_resolution",
)

_ARTIFACT_TARGET = DirectPermanentTargetSpec(
    types_any=("artifact",), controller_relation="you", token=False,
)
_ACTIVATION_PRICE = {
    "text": "{X}, {T}",
    "mana": dict.fromkeys(("GENERIC", *"WUBRGC"), 0),
    "complex_symbols": [],
    "tap_source": True,
    "untap_source": False,
    "discard_source": False,
    "sacrifice_source": False,
    "exile_source": False,
    "life_payment": 0,
    "energy_payment": 0,
    "loyalty_delta": None,
    "source_counter_removal_cost": None,
    "choices": [],
    "uncompiled_costs": [],
    "announced_x_symbols": 1,
}
_EFFECT = {
    "op": "copy_until_end_of_turn",
    ANNOUNCED_ARTIFACT_COPY_VERSION: 1,
    "source": "$source.zone_object",
    "target": "$target.0",
}


def artifact_copy_target_schema() -> dict:
    return _ARTIFACT_TARGET.to_target_schema()


def announced_artifact_copy_target_schema(announced_x: int) -> dict:
    if type(announced_x) is not int or announced_x < 0:
        raise ValueError("An artifact copy target requires a nonnegative announced X")
    return {**artifact_copy_target_schema(), "mana_value": announced_x}


def announced_artifact_copy_effect() -> dict:
    return dict(_EFFECT)


def is_announced_artifact_copy_effect(effect) -> bool:
    return isinstance(effect, Mapping) and ANNOUNCED_ARTIFACT_COPY_VERSION in effect


def is_announced_artifact_copy_program(program) -> bool:
    return program is not None and any(is_announced_artifact_copy_effect(effect) for effect in program.effects)


def validate_announced_artifact_copy_effect(effect, *, resolved=False) -> None:
    if (not is_announced_artifact_copy_effect(effect)
            or type(effect[ANNOUNCED_ARTIFACT_COPY_VERSION]) is not int
            or effect[ANNOUNCED_ARTIFACT_COPY_VERSION] != 1):
        raise ValueError("Announced artifact copy requires its exact descriptor version")
    if not resolved:
        if dict(effect) != _EFFECT:
            raise ValueError("Announced artifact copy requires a bound source and one announced target")
        return
    if (set(effect) - {"reason", "_runtime_source"} != set(_EFFECT)
            or effect["op"] != _EFFECT["op"] or type(effect["target"]) is not str or not effect["target"]
            or (effect["source"] is not None and (type(effect["source"]) is not str or not effect["source"]))):
        raise ValueError("Resolved artifact copy requires its original source and announced target")


def is_announced_artifact_copy_price(cost) -> bool:
    value = thaw_value(cost)
    return (
        isinstance(value, dict) and value == _ACTIVATION_PRICE
        and all(type(value[key]) is type(expected) for key, expected in _ACTIVATION_PRICE.items())
        and all(type(amount) is int for amount in value["mana"].values())
    )


def announced_artifact_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or "cr-115-targets" not in mechanic_ids
            or thaw_value(target_schema) != artifact_copy_target_schema()):
        return ()
    try:
        validate_announced_artifact_copy_effect(effects[0])
    except (TypeError, ValueError):
        return ()
    return ANNOUNCED_ARTIFACT_COPY_DEPENDENCIES


def announced_artifact_copy_runtime_dependencies(program):
    if not is_announced_artifact_copy_program(program):
        return ()
    required = announced_artifact_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=("cr-115-targets",),
    )
    if (not required or (program.event, program.active_zone) != ("activate", "battlefield")
            or program.event_condition is not None or program.destination is not None
            or not is_announced_artifact_copy_price(program.cost_schema)):
        raise ValueError("Announced artifact copy requires its complete ordinary X and tap activation")
    return required


def is_closed_announced_artifact_copy_program(program) -> bool:
    try:
        required = announced_artifact_copy_runtime_dependencies(program)
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
