"""Resolution-time original source power for one Firebending attack trigger."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.immutable import FrozenMap
from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec


CAPABILITY = "trigger.keyword.firebending.source_power"
MECHANIC = "firebending-source-power"
ATTACK_CONDITION = FrozenMap({"field": "card", "op": "eq", "value": "$source.ref"})
AMOUNT = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, "power")
DEPENDENCIES = (
    CAPABILITY, SCALAR_AMOUNT_CAPABILITY, "trigger.keyword.firebending.fixed",
    "trigger.event.normalized_self_attack", "trigger.placement.apnap",
)


def source_power_firebending_effect():
    return {"op": "mana", "player": "$controller", "color": "R",
            "amount": AMOUNT.to_dict(), "retain_until": "end_of_combat", "source": "$source"}


def uses_source_power_firebending(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "mana"
            and effect.get("retain_until") == "end_of_combat" and isinstance(effect.get("amount"), Mapping))


def source_power_firebending_dependencies(program):
    from .effect_shape_walkers import nested_effects

    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_source_power_firebending(effect))
    if not marked and CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or tuple(program.effects) != marked or program.handlers
            or FrozenMap(marked[0]) != FrozenMap(source_power_firebending_effect())
            or ScalarEffectAmountSpec.from_dict(marked[0]["amount"]) != AMOUNT
            or (program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or FrozenMap(program.event_condition or {}) != ATTACK_CONDITION
            or program.target_schema is not None or program.cost_schema is not None or program.trigger_limit is not None
            or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, MECHANIC}.issubset(program.coverage)):
        raise ValueError("Variable Firebending requires its original self-attack and exact retained red mana")
    return DEPENDENCIES


def bind_source_power_firebending(host, item, effect):
    if not uses_source_power_firebending(effect):
        return effect
    if (FrozenMap(effect) != FrozenMap(source_power_firebending_effect())
            or item.kind != "triggered_ability" or item.context.get("event") != "creature.attacks"):
        raise ValueError("Variable Firebending lost its original attack instruction")
    from ..scalar_effect_amounts import resolve_scalar_effect_amount

    amount = resolve_scalar_effect_amount(host, effect["amount"], item, use_phased_source_lki=True)
    return {**effect, "amount": amount} if amount else None
