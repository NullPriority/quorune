from __future__ import annotations

"""Current source-incarnation facts for the Increment intervening condition."""

from ..increment_model import increment_condition_holds,CAPABILITY,CONDITION_FIELD
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from collections.abc import Mapping


def increment_runtime_dependencies(program):
    def has_condition(value):
        if isinstance(value,Mapping):
            return value.get('field')==CONDITION_FIELD or any(has_condition(child) for child in value.values())
        return isinstance(value,(list,tuple)) and any(has_condition(child) for child in value)
    if not has_condition(program.event_condition):return ()
    if (program.event_condition!={'field':CONDITION_FIELD,'op':'truthy'}
            or program.event!='spell.cast' or 'increment' not in program.coverage
            or 'intervening_condition' not in program.coverage or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or program.target_schema is not None
            or len(program.effects)!=1 or program.effects[0]!={'op':'place_counters','card':'$source.zone_object',
                'counter':'+1/+1','amount':1,'source':'$source'}):
        raise ValueError('Increment requires its exact printed trigger and counter result')
    return (CAPABILITY,'counter.producer.fixed_effect','trigger.event.normalized_spell_cast','trigger.placement.apnap')


def current_increment_condition(host,source,context):
    resolving='source_logical_object_id' in context
    expected=context.get('source_logical_object_id')
    if resolving and (type(expected) is not str or not expected):
        raise ValueError('Increment source identity is malformed')
    if source.zone!='battlefield' or source.phased_out or resolving and source.logical_object_id!=expected:
        return False
    if not resolving and context.get('controller')!=source.controller:
        return False
    spent=context.get('mana_spent_total')
    if type(spent) is not int or spent<0:
        raise ValueError('Increment requires the committed cast payment fact')
    data=host._effective_card_data(source)
    types=host._type_parts(str(data.get('type_line') or ''))[0]
    return increment_condition_holds(is_creature='creature' in types,power=host._numeric_stat(source.object_id,'power'),
        toughness=host._numeric_stat(source.object_id,'toughness'),mana_spent=spent)
