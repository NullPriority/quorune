from __future__ import annotations

"""One announced opponent and one untargeted strongest-creature exile."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value


GREATEST_POWER_EXILE_FIELD = "opponent_greatest_power_exile"
GREATEST_POWER_EXILE_CAPABILITIES = (
    "permanent.exile.effect",
    "quantity_expression.public_characteristic_aggregate",
    "target.revalidate_resolution",
)


def greatest_power_exile_target_schema() -> dict:
    return {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}


def greatest_power_exile_effect(*, actor="$controller", opponent="$target.0") -> dict:
    return {
        "op": "choose_objects",
        "player": actor,
        GREATEST_POWER_EXILE_FIELD: {"schema_version": 1, "opponent": opponent},
    }


def validate_greatest_power_exile(effect, *, actor="$controller", opponent="$target.0") -> None:
    bound = effect.get(GREATEST_POWER_EXILE_FIELD) if isinstance(effect, Mapping) else None
    if (
        not isinstance(bound, Mapping) or type(bound.get("schema_version")) is not int
        or type(actor) is not str or not actor or type(opponent) is not str or not opponent
        or actor == opponent or thaw_value(FrozenMap(effect)) != greatest_power_exile_effect(actor=actor, opponent=opponent)
    ):
        raise ValueError("Greatest-power exile requires one exact announced opponent binding")


def greatest_power_exile_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or "exile" not in mechanic_ids or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int
            or thaw_value(FrozenMap(target_schema)) != greatest_power_exile_target_schema()):
        return ()
    try:
        validate_greatest_power_exile(effects[0])
    except (TypeError, ValueError):
        return ()
    return GREATEST_POWER_EXILE_CAPABILITIES


def greatest_power_exile_runtime_dependencies(program):
    if not any(GREATEST_POWER_EXILE_FIELD in effect for effect in program.effects):
        return ()
    owned = greatest_power_exile_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not owned:
        raise ValueError("Greatest-power exile lacks its original one-opponent target and closed result")
    return owned
