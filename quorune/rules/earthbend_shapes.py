from __future__ import annotations

from collections.abc import Mapping

from ..compiler.earthbend_templates import (
    EARTHBEND_GROUP_TARGETS, earthbend_target_schema, repeated_earthbend_target_schema,
)
from ..earthbend_model import (
    EARTHBEND_CAPABILITY, EARTHBEND_FINISH_OPERATION, EARTHBEND_MECHANIC,
    EARTHBEND_OPERATION, EARTHBEND_RETURN_OPERATION,
)
from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY, PublicQueryAmountSpec
from ..replacement.immutable import thaw_value


def earthbend_descriptor_dependencies(effect: Mapping) -> tuple[str, ...]:
    if not isinstance(effect, Mapping):
        raise ValueError("Earthbend instructions must be objects")
    operation = effect.get("op")
    if operation in (EARTHBEND_FINISH_OPERATION, EARTHBEND_RETURN_OPERATION):
        raise ValueError("Earthbend continuations require committed game events")
    if operation != EARTHBEND_OPERATION:
        return ()
    if set(effect) != {"op", "target", "amount"} or effect["target"] not in ("$target.0", *EARTHBEND_GROUP_TARGETS):
        raise ValueError("Earthbend requires its one announced land target")
    amount = effect["amount"]
    if type(amount) is int and amount >= 0:
        return (EARTHBEND_CAPABILITY,)
    spec = PublicQueryAmountSpec.from_dict(amount)
    if spec.schema_version != 1 or spec.coefficient not in (1, 2):
        raise ValueError("Earthbend requires an independently resolved nonnegative quantity")
    return EARTHBEND_CAPABILITY, PUBLIC_QUERY_AMOUNT_CAPABILITY


def _repeated_earthbend_capabilities(effects, mechanic_ids):
    if len(effects) not in (2, 3):
        return ()
    required = {"target.revalidate_resolution"}
    for effect, target in zip(effects[:2], EARTHBEND_GROUP_TARGETS, strict=True):
        if effect.get("target") != target or type(effect.get("amount")) is not int:
            return ()
        dependencies = earthbend_descriptor_dependencies(effect)
        if not dependencies:
            return ()
        required.update(dependencies)
    if len(effects) == 3:
        from .fixed_controller_effect_shapes import fixed_life_node_capabilities

        life = fixed_life_node_capabilities(
            effects=effects[2:], target_schema=None, mechanic_ids=mechanic_ids,
        )
        if not life or effects[2].get("op") != "life" or effects[2].get("player") != "$controller":
            return ()
        required.update(life)
    return tuple(sorted(required))


def earthbend_node_capabilities(*, effects, target_schema, mechanic_ids):
    if EARTHBEND_MECHANIC not in mechanic_ids or not effects or not isinstance(target_schema, Mapping):
        return ()
    if any(not isinstance(effect, Mapping) for effect in effects):
        return ()
    schema = thaw_value(target_schema)
    try:
        if schema == repeated_earthbend_target_schema():
            if any(type(group["count"]) is not int for group in schema["groups"]):
                return ()
            return _repeated_earthbend_capabilities(effects, mechanic_ids)
        if schema != earthbend_target_schema() or type(schema.get("count")) is not int:
            return ()
        if effects[0].get("target") != "$target.0":
            return ()
        required = earthbend_descriptor_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()
    if not required:
        return ()
    if len(effects) == 1:
        return (*required, "target.revalidate_resolution")
    if len(effects) != 2 or effects[1] != {"op": "untap", "card": "$target.0"}:
        return ()
    from .tap_state_capability_shapes import targeted_tap_state_node_capabilities

    untap = targeted_tap_state_node_capabilities(
        effects=effects[1:], target_schema=target_schema, mechanic_ids=mechanic_ids,
    )
    return tuple(sorted({*required, *untap})) if untap else ()


def is_closed_earthbend_program(program):
    required = earthbend_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required).issubset(program.capability_dependencies)
