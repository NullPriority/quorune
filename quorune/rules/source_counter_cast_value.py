"""Typed cast-time mana-value comparison with public source counters."""
from collections.abc import Mapping
from dataclasses import dataclass
import math,re
FIELD = 'cast_mana_value_matches_source_counter'
CAPABILITY = 'trigger.condition.cast_mana_value_source_counter'
@dataclass(frozen=True,slots=True)
class SourceCounterCastValueSpec:
    counter_name: str
    schema_version: int = 1
    def __post_init__(self):
        if (type(self.schema_version)is not int or self.schema_version!=1 or type(self.counter_name)is not str or len(self.counter_name)>40
                or re.fullmatch(r'[a-z][a-z0-9]*(?:[ -][a-z0-9]+)*',self.counter_name)is None):
            raise ValueError('Cast comparison requires one canonical public counter name')
    def condition(self):return {'field':FIELD,'op':'truthy','value':True,'schema_version':1,'counter':self.counter_name}
    @classmethod
    def from_condition(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'field','op','value','schema_version','counter'}
                or value['field']!=FIELD or value['op']!='truthy' or value['value']is not True):
            raise ValueError('Cast-source counter condition fields are closed')
        return cls(value['counter'],value['schema_version'])
    @property
    def capabilities(self):return (CAPABILITY,'trigger.event.normalized_spell_cast')
    def matches(self,source,context):
        counters=getattr(source,'counters',None);value=context.get('mana_value')
        if not isinstance(counters,Mapping):raise ValueError('Source counter information is unavailable')
        count=counters.get(self.counter_name,0)
        if type(count)is not int or count<0 or type(value)not in (int,float) or not math.isfinite(value) or value<0:
            raise ValueError('Cast comparison needs nonnegative represented numeric facts')
        return value==count

def source_counter_cast_runtime_dependencies(program):
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            if value.get('field')==FIELD:
                spec=SourceCounterCastValueSpec.from_condition(value)
                if program.event!='spell.cast' or program.active_zone!='battlefield':
                    raise ValueError('Counter comparison requires a battlefield cast subscription')
                required.update(spec.capabilities)
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit(program.event_condition);return tuple(sorted(required))
