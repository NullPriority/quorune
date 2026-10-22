from __future__ import annotations

from collections.abc import Mapping

LAND_ENTRY_CONDITION_CAPABILITY='condition.land_entered_this_turn'
LAND_ENTRY_FACT='controller_lands_entered_this_turn'


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
