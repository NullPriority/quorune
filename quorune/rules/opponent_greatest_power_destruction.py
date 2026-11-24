from __future__ import annotations

"""Closed source-controller choice of one greatest-power creature per opponent."""

from collections.abc import Mapping


GREATEST_POWER_DESTRUCTION_FIELD = "opponent_greatest_power_destruction"
GREATEST_POWER_DESTRUCTION_CAPABILITIES = (
    "permanent.destroy.fixed_set",
    "quantity_expression.public_characteristic_aggregate",
)


def greatest_power_destruction_effect(*, actor: str = "$controller") -> dict:
    return {"op": "choose_objects", "player": actor, GREATEST_POWER_DESTRUCTION_FIELD: 1}


def validate_greatest_power_destruction(effect: Mapping, *, actor: str = "$controller") -> None:
    if (
        not isinstance(effect, Mapping)
        or not isinstance(actor, str) or not actor
        or type(effect.get(GREATEST_POWER_DESTRUCTION_FIELD)) is not int
        or dict(effect) != greatest_power_destruction_effect(actor=actor)
    ):
        raise ValueError("Greatest-power destruction requires the resolving controller and its exact descriptor")


def greatest_power_destruction_node_capabilities(*, effects, target_schema, mechanic_ids) -> tuple[str, ...]:
    if target_schema is not None or len(effects) != 1 or "destroy" not in mechanic_ids:
        return ()
    try:
        validate_greatest_power_destruction(effects[0])
    except (TypeError, ValueError):
        return ()
    return GREATEST_POWER_DESTRUCTION_CAPABILITIES


def greatest_power_destruction_runtime_dependencies(program) -> tuple[str, ...]:
    if not any(GREATEST_POWER_DESTRUCTION_FIELD in effect for effect in program.effects):
        return ()
    required = greatest_power_destruction_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Greatest-power destruction requires its standalone nontargeted result")
    return required
