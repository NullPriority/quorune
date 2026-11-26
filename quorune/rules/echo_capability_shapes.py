from __future__ import annotations

"""Closed CardProgram capability shape for ordinary fixed-mana Echo."""

from typing import Any, Iterable, Mapping, Sequence

from ..echo import DISCARD_ECHO_CAPABILITY, DISCARD_ECHO_TEMPLATE, echo_upkeep_condition, validate_discard_echo_effect
from ..fixed_mana_abilities import MANA_COST_KEYS


def fixed_mana_echo_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    event_condition: Mapping[str, Any] | None,
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership only for one ordinary fixed-mana Echo trigger."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "echo" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    cost = effect.get("cost")
    if (
        target_schema is not None
        or set(effect) != {"op", "player", "source", "cost"}
        or effect.get("op") != "echo_upkeep"
        or effect.get("player") != "$controller"
        or effect.get("source") != "$source"
        or not isinstance(cost, Mapping)
        or set(cost) != set(MANA_COST_KEYS)
        or any(type(amount) is not int or amount < 0 for amount in cost.values())
    ):
        return ()
    if dict(event_condition or {}) != echo_upkeep_condition():
        return ()
    return ("trigger.keyword.echo.fixed_mana",)


def discard_echo_node_capabilities(*, effects, event_condition, target_schema, mechanic_ids):
    if ("echo" not in mechanic_ids or len(effects) != 1 or target_schema is not None
            or dict(event_condition or {}) != echo_upkeep_condition()):
        return ()
    try:
        validate_discard_echo_effect(effects[0], player="$controller", source="$source")
    except (TypeError, ValueError, KeyError):
        return ()
    return (DISCARD_ECHO_CAPABILITY,)


def discard_echo_runtime_dependencies(program):
    marked = program.provenance.get("template_id") == DISCARD_ECHO_TEMPLATE
    uses_payment = any(effect.get("op") == "echo_upkeep" and ("payment" in effect or "schema_version" in effect)
                       for effect in program.effects)
    if not marked and not uses_payment:
        return ()
    required = discard_echo_node_capabilities(
        effects=program.effects, event_condition=program.event_condition,
        target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not marked or not required or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or program.cost_schema is not None or program.handlers or "intervening_condition" not in program.coverage):
        raise ValueError("Discard Echo requires the complete intervening upkeep trigger")
    return required


__all__ = ["discard_echo_node_capabilities", "discard_echo_runtime_dependencies", "fixed_mana_echo_node_capabilities"]
