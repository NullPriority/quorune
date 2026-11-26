"""Close an opposing owner's life-loss result on a nonland entry occurrence."""

from collections.abc import Mapping

from .effect_shape_walkers import nested_effects
from ..replacement.immutable import thaw_value
from ..scalar_effect_amount_model import (
    SCALAR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_KIND, ScalarAmountOrigin, ScalarEffectAmountSpec,
)


OPPOSING_OWNER_ENTRY_LIFE_CAPABILITY = "life.loss.opposing_owner_entry_mana_value"
OPPOSING_OWNER_ENTRY_LIFE_MECHANIC = "opposing-owner-entry-mana-value-life"
OPPOSING_OWNER_ENTRY_VARIANT = "opposing_owner_nonland_entry_under_control"
OPPOSING_OWNER_ENTRY_EVENT = "permanent.enter"
_OPPOSING_OWNER = "$context.owner"
_REQUIREMENTS = (
    OPPOSING_OWNER_ENTRY_LIFE_CAPABILITY,
    SCALAR_AMOUNT_CAPABILITY,
    "life.change.effect",
    "trigger.event.normalized_zone_change",
    "trigger.placement.apnap",
)


def opposing_owner_entry_condition():
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "owner", "op": "ne", "value": "$source.controller"},
        {"not": {"field": "types", "op": "contains_any", "value": ["land"]}},
    ]}


def opposing_owner_entry_life_effect():
    return {
        "op": "lose_life",
        "player": _OPPOSING_OWNER,
        "amount": ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_OBJECT, characteristic="mana_value").to_dict(),
    }


def uses_opposing_owner_entry_life(effect):
    amount = effect.get("amount")
    return (
        effect.get("op") == "lose_life" and effect.get("player") == _OPPOSING_OWNER
        and isinstance(amount, Mapping) and amount.get("kind") == SCALAR_AMOUNT_KIND
    )


def _closed_effect(effects, target_schema, cost_schema):
    return (
        target_schema is None and cost_schema is None and len(effects) == 1
        and thaw_value(effects[0]) == opposing_owner_entry_life_effect()
        and ScalarEffectAmountSpec.from_dict(effects[0]["amount"])
        == ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_OBJECT, characteristic="mana_value")
    )


def opposing_owner_entry_node_capabilities(effects, target_schema, cost_schema, mechanics):
    if OPPOSING_OWNER_ENTRY_LIFE_MECHANIC not in mechanics:
        return None
    try:
        return _REQUIREMENTS if _closed_effect(effects, target_schema, cost_schema) else ()
    except (TypeError, ValueError):
        return ()


def opposing_owner_entry_runtime_dependencies(program):
    marked = OPPOSING_OWNER_ENTRY_LIFE_MECHANIC in program.coverage
    effect_used = any(uses_opposing_owner_entry_life(effect) for effect in nested_effects((*program.effects, *program.handlers)))
    if not marked and not effect_used:
        return ()
    if (
        not marked or (program.event, program.active_zone) != (OPPOSING_OWNER_ENTRY_EVENT, "battlefield")
        or thaw_value(program.event_condition) != opposing_owner_entry_condition()
        or program.handlers or program.trigger_limit is not None
        or not _closed_effect(program.effects, program.target_schema, program.cost_schema)
    ):
        raise ValueError("Opposing-owner life loss requires the exact nonland entry under the source controller")
    return _REQUIREMENTS


def bind_opposing_owner_entry_life(state, item, effect):
    """Keep the original owner and let the scalar owner read current or last known mana value."""
    owner = item.context.get("owner")
    player = state.players.get(owner) if type(owner) is str else None
    if (
        item.kind != "triggered_ability" or item.context.get("event") != OPPOSING_OWNER_ENTRY_EVENT
        or player is None or owner == item.controller or item.context.get("controller") != item.controller
    ):
        raise ValueError("Opposing-owner life loss is missing its committed entry and original owner")
    return effect if player.in_game else None
