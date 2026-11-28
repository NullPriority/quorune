"""Convert a sealed current-turn opponent life-loss total to postcombat mana."""

from collections.abc import Mapping

from ..public_history_query_model import (
    OPPONENT_LIFE_LOSS_HISTORY_CAPABILITY, opponent_life_loss_including_departed_query,
    public_history_query_value,
)
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects


CAPABILITY = "mana.production.postcombat_opponent_life_loss"
TEMPLATE = "postcombat-opponent-life-loss-mana-v1"
VARIANT = "postcombat_opponent_life_loss_mana"
AMOUNT_KIND = "postcombat_opponent_life_loss_mana_amount"
REQUIREMENTS = (CAPABILITY, OPPONENT_LIFE_LOSS_HISTORY_CAPABILITY,
                "mana.production.fixed_resolution", "trigger.placement.apnap")


def condition():
    return {"all": [
        {"field": "phase", "op": "eq", "value": "postcombat_main"},
        {"field": "step", "op": "eq", "value": "main"},
        {"field": "player", "op": "eq", "value": "$source.controller"},
    ]}


def amount_descriptor():
    return {"kind": AMOUNT_KIND, "schema_version": 1}


def effect(color):
    if type(color) is not str or color not in tuple("WUBRGC"):
        raise ValueError("Postcombat life-loss mana needs exactly one mana type")
    return {"op": "mana", "player": "$controller", "color": color,
            "amount": amount_descriptor(), "source": "$source"}


def uses_effect(value):
    amount = value.get("amount")
    return isinstance(amount, Mapping) and amount.get("kind") == AMOUNT_KIND


def effect_is_closed(value):
    if not uses_effect(value) or type(value["amount"].get("schema_version")) is not int:
        return False
    try:
        return thaw_value(value) == effect(value.get("color"))
    except ValueError:
        return False


def program_dependencies(program):
    uses = any(uses_effect(value) for value in nested_effects((*program.effects, *program.handlers)))
    if not uses and CAPABILITY not in program.capability_dependencies:
        return ()
    if ((program.event, program.active_zone) != ("step.begin", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(condition())
            or program.handlers or program.target_schema is not None or program.cost_schema is not None
            or program.trigger_limit is not None or len(program.effects) != 1
            or not effect_is_closed(program.effects[0])):
        raise ValueError("Postcombat mana needs its exact untargeted phase and historical amount")
    return REQUIREMENTS


def resolve_amount(host, value, item):
    if (not isinstance(value, Mapping) or type(value.get("schema_version")) is not int
            or thaw_value(value) != amount_descriptor()):
        raise ValueError("Postcombat mana amount has an unknown shape")
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True
            or program is None or not host.semantic_program_is_current_trusted(program)
            or not program_dependencies(program)):
        raise ValueError("The original postcombat mana trigger is unavailable")
    total = public_history_query_value(host.state, item.controller, opponent_life_loss_including_departed_query())
    if type(total) is not int or total < 0:
        raise ValueError("Current-turn life loss is unavailable")
    return total
