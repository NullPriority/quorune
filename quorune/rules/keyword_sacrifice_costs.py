"""Independent runtime minima for explicit keyword-sacrifice prices."""
from collections.abc import Mapping
from ..keyword_sacrifice_cost_model import (KEYWORD_SACRIFICE_COST_VERSION,
    KEYWORD_SACRIFICE_CHOICE_FIELD,KEYWORD_SACRIFICE_CAPABILITY)


def keyword_sacrifice_payload_dependencies(value):
    if value.get('kind')=='zone_change' and value.get('schema_version')==KEYWORD_SACRIFICE_COST_VERSION:
        from .casting_additional_costs import FixedZoneChangeAdditionalCost
        FixedZoneChangeAdditionalCost.from_descriptor(value)
        return (KEYWORD_SACRIFICE_CAPABILITY,)
    if KEYWORD_SACRIFICE_CHOICE_FIELD not in value:return ()
    if type(value[KEYWORD_SACRIFICE_CHOICE_FIELD]) is not int or value[KEYWORD_SACRIFICE_CHOICE_FIELD]!=KEYWORD_SACRIFICE_COST_VERSION:
        raise ValueError('Unsupported keyword-sacrifice choice version')
    from ..activation_cost_choice import CostChoice
    if 'k' in value:
        fields={'k','n','z','q',KEYWORD_SACRIFICE_CHOICE_FIELD}
        if set(value) not in (fields,fields|{'other'}) or not isinstance(value['q'],Mapping):
            raise ValueError('Keyword-sacrifice compact price fields are closed')
        if 'other' in value and (type(value['other']) is not int or value['other']!=1):
            raise ValueError('Explicit source exclusion must be exactly one')
        choice=CostChoice(kind=value['k'],count=value['n'],zone=value['z'],predicate=value['q'],
            another='other' in value,sacrifice_schema_version=value[KEYWORD_SACRIFICE_CHOICE_FIELD])
    else:choice=CostChoice.from_dict(value)
    if choice.fixed_zone_change_cost().schema_version!=KEYWORD_SACRIFICE_COST_VERSION:
        raise ValueError('Keyword sacrifice lost its explicit typed cost owner')
    return ('activation.selected_zone_change.fixed',KEYWORD_SACRIFICE_CAPABILITY)
