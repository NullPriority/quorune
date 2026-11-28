from __future__ import annotations

from collections.abc import Mapping

LAND_ENTRY_CONDITION_CAPABILITY='condition.land_entered_this_turn'
LAND_ENTRY_FACT='controller_lands_entered_this_turn'

OTHER_LAND_ARRIVAL_FIELD = "entry_controller_had_other_land_arrival"
OTHER_LAND_ARRIVAL_COVERAGE = "opponent-land-entry-history"
OTHER_LAND_ARRIVAL_VARIANT = "opponent_land_enters_with_other_arrival"
OTHER_LAND_ARRIVAL_EVENT = "land.enter"

ENTRANT_LAND_ADVANTAGE_CAPABILITY = "condition.player.entering_opponent_land_advantage"
ENTRANT_LAND_ADVANTAGE_COVERAGE = "entering-opponent-land-advantage"
ENTRANT_LAND_ADVANTAGE_FIELD = "entering_controller_has_more_lands"

_OPPONENT_ARRIVAL = {"field": "controller", "op": "ne", "value": "$source.controller"}
_OTHER_ARRIVAL = {"field": OTHER_LAND_ARRIVAL_FIELD, "op": "eq", "value": True}


def opponent_other_land_arrival_condition():
    return {"all": [dict(_OPPONENT_ARRIVAL), dict(_OTHER_ARRIVAL)]}


def uses_other_land_arrival_condition(value):
    if isinstance(value, Mapping):
        return value.get("field") == OTHER_LAND_ARRIVAL_FIELD or any(
            uses_other_land_arrival_condition(child) for child in value.values()
        )
    if isinstance(value, (tuple, list)):
        return any(uses_other_land_arrival_condition(child) for child in value)
    return False


def other_land_arrival_leaf_is_closed(condition):
    return condition == _OTHER_ARRIVAL and condition.get("value") is True


def entrant_land_advantage_condition():
    return {"all": [dict(_OPPONENT_ARRIVAL), {
        "field": ENTRANT_LAND_ADVANTAGE_FIELD, "op": "eq", "value": True,
    }]}


def uses_entrant_land_advantage(value):
    if isinstance(value, Mapping):
        return value.get("field") == ENTRANT_LAND_ADVANTAGE_FIELD or any(
            uses_entrant_land_advantage(child) for child in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(uses_entrant_land_advantage(child) for child in value)
    return False


def entrant_land_advantage_leaf_is_closed(condition):
    return condition == entrant_land_advantage_condition()["all"][1] and condition["value"] is True


def other_land_arrival_damage_effect():
    return {"op": "damage", "source": "$source", "target": "$context.controller", "amount": 3}


def land_entry_condition_capabilities(value):
    if isinstance(value,Mapping):
        if value.get('fact')==LAND_ENTRY_FACT:
            from .continuous_conditions import FixedPublicStateConditionSpec
            condition=FixedPublicStateConditionSpec.from_dict(value)
            if (condition.schema_version!=4 or type(condition.amount) is not int or condition.amount!=1
                    or condition.kind.value!='public_fact_at_least' or condition.fact_parameter is not None):
                raise ValueError('Land entry condition requires one closed current-turn presence fact')
            return (LAND_ENTRY_CONDITION_CAPABILITY,)
        return tuple(sorted({cap for child in value.values() for cap in land_entry_condition_capabilities(child)}))
    if isinstance(value,(list,tuple)):
        return tuple(sorted({cap for child in value for cap in land_entry_condition_capabilities(child)}))
    return ()
