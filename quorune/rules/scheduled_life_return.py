"""A fixed life price and one controlled return from a sealed upkeep."""

from collections.abc import Mapping

from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .scheduled_player_shapes import SCHEDULED_PLAYER_CAPABILITY, SCHEDULED_PLAYER_MECHANIC

SCHEDULED_LIFE_RETURN_CAPABILITY = "effect.choice.upkeep_player_fixed_life_return"
SCHEDULED_LIFE_RETURN_FIELD = "scheduled_upkeep_life_return"
UPKEEP_PARTICIPANT = "$context.player"
UPKEEP_CONDITION = {"all": [{"field": "step", "op": "eq", "value": "upkeep"}]}
_FIELDS = {"op", "schema_version", "player", "payment", "effects", "declined_effects", SCHEDULED_LIFE_RETURN_FIELD}


def controlled_return_for_payer(caster, payer):
    from ..compiler.fixed_entry_return_requirements import FixedEntryReturnRequirementSpec

    effect, = FixedEntryReturnRequirementSpec(
        count=1, predicate=ObjectQuerySpec(zones=("battlefield",)),
        excludes_source=False, sacrifice_source_unless_paid=False,
    ).effects
    return {**effect, "actor": caster, "players": [payer]}


def is_scheduled_life_return(effect):
    return isinstance(effect, Mapping) and SCHEDULED_LIFE_RETURN_FIELD in effect


def scheduled_life_return_parts(effect, *, controller, payer):
    if (not is_scheduled_life_return(effect) or set(effect) != _FIELDS
            or effect["op"] != "offer_optional_mana_payment"
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 2
            or type(effect[SCHEDULED_LIFE_RETURN_FIELD]) is not int or effect[SCHEDULED_LIFE_RETURN_FIELD] != 1
            or effect["player"] != payer
            or not isinstance(effect["effects"], (list, tuple)) or effect["effects"]
            or not isinstance(effect["declined_effects"], (list, tuple)) or len(effect["declined_effects"]) != 1):
        raise ValueError("Upkeep life return needs one fixed price and one bound alternative")
    cost = FixedEffectPaymentSpec.from_dict(effect["payment"])
    result = effect["declined_effects"][0]
    if (cost.kind != "life" or cost.amount < 1 or not isinstance(result, Mapping)
            or type(result.get("count")) is not int
            or ObjectQuerySpec.from_dict(thaw_value(result.get("predicate"))) != ObjectQuerySpec(zones=("battlefield",))
            or thaw_value(result) != controlled_return_for_payer(controller, payer)):
        raise ValueError("Upkeep life decline must return one permanent controlled by the payer")
    return cost, result


def scheduled_life_return_node_dependencies(effect, target_schema, mechanic_ids):
    required_mechanics = {SCHEDULED_PLAYER_MECHANIC, FIXED_EFFECT_PAYMENT_MECHANIC}
    if target_schema is not None or not required_mechanics <= set(mechanic_ids):
        return ()
    try:
        cost, _ = scheduled_life_return_parts(effect, controller="$controller", payer=UPKEEP_PARTICIPANT)
    except (TypeError, ValueError, KeyError):
        return ()
    return tuple(sorted({
        SCHEDULED_PLAYER_CAPABILITY, SCHEDULED_LIFE_RETURN_CAPABILITY,
        "choice.controller.fixed_return_owner_hand", *cost.capabilities,
    }))


def scheduled_life_return_payload_dependencies(effect):
    required = scheduled_life_return_node_dependencies(
        effect, None, (SCHEDULED_PLAYER_MECHANIC, FIXED_EFFECT_PAYMENT_MECHANIC),
    )
    if not required:
        raise ValueError("The upkeep life return has no typed participant authority")
    return required


def scheduled_life_return_program_dependencies(program, effect):
    if ((program.event, program.active_zone) != ("step.begin", "battlefield")
            or thaw_value(program.event_condition) != UPKEEP_CONDITION
            or len(program.effects) != 1 or effect != program.effects[0]
            or getattr(program, "cost_schema", None) is not None or getattr(program, "handlers", ())):
        raise ValueError("The upkeep life return requires the sealed each-player upkeep")
    required = scheduled_life_return_node_dependencies(effect, program.target_schema, program.coverage)
    if not required:
        raise ValueError("The upkeep life return lacks a closed payment or controlled result")
    return required


def has_closed_scheduled_life_return(program):
    """Distinguish this validated occurrence and its nested return from caster choices."""
    if len(program.effects) != 1 or not is_scheduled_life_return(program.effects[0]):
        return False
    return bool(scheduled_life_return_program_dependencies(program, program.effects[0]))
