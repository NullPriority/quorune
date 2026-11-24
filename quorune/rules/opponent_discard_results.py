"""Closed source-discard trigger bodies and typed admission."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
    FixedPublicStateConditionKind, FixedPublicStateConditionSpec,
)
from ..delayed_object_model import (
    DELAYED_OBJECT_CAPABILITY, DELAYED_OBJECT_MECHANIC, GRAVEYARD_COUNTER_RETURN_VERSION,
    GRAVEYARD_RETURN_VERSION, SCHEDULE_OBJECT_ACTION,
)
from ..discard_effect_origin import OPPONENT_SOURCE_DISCARD_EVENT, PUBLIC_DISCARD_DESTINATION_SCOPE
from ..fixed_token_production import FIXED_TOKEN_PRODUCTION_MECHANIC_ID
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .token_creation_capability_shapes import (
    fixed_token_creation_node_capabilities, token_creation_component_mechanics,
)

OPPONENT_DISCARD_CAPABILITY = "trigger.source.opponent_effect_discard"
OPPONENT_DISCARD_DAMAGE_CAPABILITY = "trigger.source.opponent_effect_discard_damage"
OPPONENT_DISCARD_DELAYED_RETURN_CAPABILITY = "trigger.source.opponent_effect_discard_delayed_return"
OPPONENT_DISCARD_DELAYED_COUNTER_RETURN_CAPABILITY = "trigger.source.opponent_effect_discard_delayed_counter_return"
OPPONENT_DISCARD_PLAINS_TOKEN_CAPABILITY = "trigger.source.opponent_effect_discard_controlled_plains_token"
OPPONENT_DISCARD_SELF_EVENT = OPPONENT_SOURCE_DISCARD_EVENT + ".self"
_CAUSER = "$context.causing_player"
_TOKEN_MECHANICS = {FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "cr-111-tokens"}
_ANY_DAMAGE_TARGET = {"zones": ["player", "battlefield"], "categories": ["player", "permanent"],
                      "predicate": "damageable", "count": 1}


def return_discarded_source_effect():
    return {
        "op": "move_if_in_zone",
        "card": "$context.card",
        "from": "$context.to",
        "expected_object_identity": "$context.discarded_logical_object_id",
        "destination": "hand",
    }


def discard_causer_life_loss_effect(amount):
    return {"op": "lose_life", "player": _CAUSER, "amount": amount}


def opponent_discard_result_capabilities(effect, *, target_schema=None, mechanic_ids=()):
    if not isinstance(effect, Mapping):
        return ()
    value = thaw_value(effect)
    if target_schema is not None:
        from .damage_capability_shapes import fixed_damage_node_capabilities

        schema = thaw_value(target_schema)
        if schema != _ANY_DAMAGE_TARGET or type(schema.get("count")) is not int:
            return ()
        dependencies = fixed_damage_node_capabilities(effects=(value,), target_schema=schema, mechanic_ids=mechanic_ids)
        return (OPPONENT_DISCARD_DAMAGE_CAPABILITY, *dependencies) if dependencies else ()
    if value == return_discarded_source_effect():
        return ("permanent.return.owner_hand",)
    amount = value.get("amount")
    if type(amount) is int and amount > 0 and value == discard_causer_life_loss_effect(amount):
        return ("life.change.effect",)
    if value.get("op") == "create_token" and not any(key in value for key in ("copy_of", "copy_definition")):
        return fixed_token_creation_node_capabilities(
            effects=(value,), target_schema=None,
            mechanic_ids=token_creation_component_mechanics((value,), _TOKEN_MECHANICS),
        )
    return ()


def opponent_discard_capabilities(effect, *, target_schema=None, mechanic_ids=()):
    required = opponent_discard_result_capabilities(effect, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if not required:
        return ()
    return tuple(sorted({
        OPPONENT_DISCARD_CAPABILITY, "zone.discard.typed_cause",
        "trigger.event.normalized_zone_change", "trigger.placement.apnap", *required,
    }))


def discard_life_and_delayed_return_effects(amount):
    return (
        {"op": "life", "player": "$controller", "delta": amount},
        {"op": SCHEDULE_OBJECT_ACTION, "schema_version": GRAVEYARD_RETURN_VERSION,
         "action": "bounce", "object": "$source.zone_object", "timing": "next_end_step"},
    )


def controlled_plains_discard_condition():
    query = ObjectQuerySpec(zones=("battlefield",), types_all=("land",), subtypes_all=("plains",))
    condition = FixedPublicStateConditionSpec(
        kind=FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST, amount=1,
        quantity=CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE, query=query),
        schema_version=2,
    )
    return {"field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, "op": "truthy",
            "value": True, "condition": condition.to_dict()}


def controlled_plains_discard_token_dependencies(effects, *, event_condition, target_schema, coverage):
    if (
        target_schema is not None or len(effects) != 1
        or not isinstance(effects[0], Mapping) or effects[0].get("op") != "create_token"
        or thaw_value(event_condition) != controlled_plains_discard_condition()
        or not {FIXED_PUBLIC_STATE_INTERVENING_COVERAGE, "intervening_condition"} <= set(coverage)
    ):
        return ()
    required = opponent_discard_capabilities(effects[0])
    if not required:
        return ()
    return (*required, OPPONENT_DISCARD_PLAINS_TOKEN_CAPABILITY, "trigger.condition.fixed_public_state")


def discard_delayed_counter_return_effect():
    return {"op": SCHEDULE_OBJECT_ACTION, "schema_version": GRAVEYARD_COUNTER_RETURN_VERSION,
            "action": "return_with_counter", "object": "$source.zone_object", "timing": "next_end_step"}


def opponent_discard_group_capabilities(effects, *, target_schema=None, mechanic_ids=(), event_condition=None):
    if event_condition is not None:
        return controlled_plains_discard_token_dependencies(
            effects, event_condition=event_condition, target_schema=target_schema, coverage=mechanic_ids,
        )
    if len(effects) == 1:
        value = thaw_value(effects[0])
        if (target_schema is None and DELAYED_OBJECT_MECHANIC in mechanic_ids and isinstance(value, Mapping)
                and type(value.get("schema_version")) is int and value == discard_delayed_counter_return_effect()):
            return tuple(sorted((
                OPPONENT_DISCARD_CAPABILITY, OPPONENT_DISCARD_DELAYED_COUNTER_RETURN_CAPABILITY,
                DELAYED_OBJECT_CAPABILITY, "zone.return.fixed_event_battlefield", "zone.reanimate.entry_counters",
                "zone.discard.typed_cause", "trigger.event.normalized_zone_change", "trigger.placement.apnap",
                "zone.change.destination_replacement",
            )))
        return opponent_discard_capabilities(
            effects[0], target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    if (len(effects) != 2 or target_schema is not None or DELAYED_OBJECT_MECHANIC not in mechanic_ids
            or not all(isinstance(effect, Mapping) for effect in effects)):
        return ()
    life, return_effect = (thaw_value(effect) for effect in effects)
    amount = life.get("delta")
    if (type(amount) is not int or amount < 1 or type(return_effect.get("schema_version")) is not int
            or (life, return_effect) != discard_life_and_delayed_return_effects(amount)):
        return ()
    return tuple(sorted((
        OPPONENT_DISCARD_CAPABILITY, OPPONENT_DISCARD_DELAYED_RETURN_CAPABILITY,
        DELAYED_OBJECT_CAPABILITY, "zone.discard.typed_cause",
        "trigger.event.normalized_zone_change", "trigger.placement.apnap",
        "life.change.effect", "permanent.return.owner_hand", "zone.change.destination_replacement",
    )))


def opponent_discard_runtime_dependencies(program):
    if (program.event not in {OPPONENT_SOURCE_DISCARD_EVENT, OPPONENT_DISCARD_SELF_EVENT}
            and program.active_zone != PUBLIC_DISCARD_DESTINATION_SCOPE):
        return ()
    if ((program.event, program.active_zone) != (OPPONENT_DISCARD_SELF_EVENT, PUBLIC_DISCARD_DESTINATION_SCOPE)
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or getattr(program, "cost_schema", None) is not None or getattr(program, "handlers", ())):
        raise ValueError("Opponent discard requires the sealed public source occurrence")
    required = opponent_discard_group_capabilities(
        program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        event_condition=program.event_condition,
    )
    if not required:
        raise ValueError("Opponent discard has no closed result owner")
    return required


def is_closed_opponent_discard_program(program):
    try:
        required = opponent_discard_runtime_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
