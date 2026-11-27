from __future__ import annotations

"""Paid return of a designated battlefield commander owned by the activator."""

from collections.abc import Mapping

from ..activation_condition_model import ActivationCondition, ActivationConditionKind
from ..replacement.immutable import FrozenMap, thaw_value
from .commander_lifelink import (
    commander_lifelink_price_is_closed, commander_lifelink_runtime_dependencies,
)


CAPABILITY = "permanent.return.owned_commander_to_hand"
TEMPLATE = "paid-own-commander-battlefield-return-v1"
MECHANICS = ("return-to-owner-hand", "cr-115-targets")
_RETURN_TEXT = "return target commander you own from the battlefield to your hand."
_RESTRICTION = "activate only during your turn."
_TARGET = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
    "commander": True, "owner_relation": "you",
})
_EFFECT = FrozenMap({"op": "bounce", "card": "$target.0"})


def _activation_matches(ability):
    from ..compiler.activated_costs import activated_ability_cost

    return (
        " ".join(ability.effect_text.casefold().split()) == f"{_RETURN_TEXT} {_RESTRICTION}"
        and ability.zones == ("battlefield",) and not ability.mana_ability and not ability.sorcery_speed
        and ability.activation_limit is None
        and ability.activation_conditions == (ActivationCondition(ActivationConditionKind.CONTROLLERS_TURN),)
        and commander_lifelink_price_is_closed(activated_ability_cost(ability))
    )


def own_commander_return_activation_result(ability, text, *, public_timing):
    if (public_timing is not None or " ".join(text.casefold().split()) != _RETURN_TEXT
            or not _activation_matches(ability)):
        return None
    return TEMPLATE, (thaw_value(_EFFECT),), thaw_value(_TARGET), MECHANICS


def own_commander_return_node_capabilities(*, effects, active_zone, target_schema, mechanics, cost_schema):
    if (active_zone != "battlefield" or len(effects) != 1 or FrozenMap(effects[0]) != _EFFECT
            or not isinstance(target_schema, Mapping) or FrozenMap(target_schema) != _TARGET
            or target_schema.get("commander") is not True or type(target_schema.get("count")) is not int
            or not set(MECHANICS).issubset(mechanics)
            or not commander_lifelink_price_is_closed(cost_schema)):
        return ()
    return (
        CAPABILITY, "permanent.return.owner_hand", "state_query.commander.permanent_designation",
        "target.revalidate_resolution", "zone.change.destination_replacement",
    )


def own_commander_return_runtime_dependencies(program):
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    schema = program.target_schema
    marked = CAPABILITY in program.capability_dependencies or (
        isinstance(schema, Mapping) and schema.get("commander") is True
        and any(effect.get("op") == "bounce" for effect in program.effects)
    )
    if not marked:
        return ()
    if (program.event != "activate" or program.event_condition is not None
            or len(program.handlers) != 1
            or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Owned commander return requires its original active ability catalog")
    abilities = activated_abilities_from_descriptors(program.handlers)
    required = own_commander_return_node_capabilities(
        effects=program.effects, active_zone=program.active_zone, target_schema=schema,
        mechanics=program.coverage, cost_schema=program.cost_schema,
    )
    if (not required or len(abilities) != 1 or not _activation_matches(abilities[0])
            or program.ability_id != f"ability:{abilities[0].ability_id}"):
        raise ValueError("Owned commander return requires the exact paid turn window and target")
    return required


def paid_commander_target_runtime_dependencies(program):
    return (*commander_lifelink_runtime_dependencies(program), *own_commander_return_runtime_dependencies(program))


def generated_commander_return_is_closed(program):
    from .commander_return_choice import is_closed_commander_return_program

    if is_closed_commander_return_program(program):
        return True
    try:
        required = own_commander_return_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
