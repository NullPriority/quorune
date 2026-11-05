"""CR122.1h: a counter-derived ordinary destination replacement."""
from collections.abc import Mapping
from ..replacement_effects import ReplacementClass,ReplacementEffect,SetField

FINALITY_COUNTER_CAPABILITY='counter.rule.finality'


def finality_counter_dependencies(value):
    def named(candidate):return type(candidate)is str and candidate.strip().casefold()=='finality'
    if isinstance(value,Mapping):
        if (any(named(value.get(key)) for key in ('counter','counter_name'))
                or isinstance(value.get('counters'),Mapping) and any(named(key) for key in value['counters'])):
            return (FINALITY_COUNTER_CAPABILITY,)
        return tuple(sorted({cap for child in value.values() for cap in finality_counter_dependencies(child)}))
    if isinstance(value,(list,tuple)):
        return tuple(sorted({cap for child in value for cap in finality_counter_dependencies(child)}))
    return ()


def finality_counter_runtime_dependencies(program):
    return finality_counter_dependencies((program.effects,program.handlers))


def finality_counter_replacement(source):
    count=source.counters.get('finality',0)
    if type(count)is not int or count<0:
        raise ValueError('Finality counter quantity must be a nonnegative integer')
    if not count or source.zone!='battlefield' or source.phased_out:return None
    return ReplacementEffect(effect_id='rule:finality:'+source.logical_object_id,
        source_id=source.ref,event_kind='zone.change',replacement_class=ReplacementClass.OTHER,
        conditions={'origin':{'eq':'battlefield'},'destination':{'eq':'graveyard'},
            'object_ref':{'eq':source.ref},'logical_object_id':{'eq':source.logical_object_id}},
        operations=(SetField('destination','exile'),),label=source.ref+': finality exiles this permanent instead')
