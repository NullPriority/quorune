from __future__ import annotations

"""Typed static spell costs that inspect the announced target set."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

TARGETED_SPELL_COST_CAPABILITY='casting.cost.targeted_fixed_generic'
TARGETED_SPELL_COST_HANDLER='modification.cast-cost.targeted-fixed.v1'
TARGETED_SPELL_COST_EVENT='cast.cost.modify'


class TargetedCostSubject(StrEnum):
    SOURCE='source'
    CONTROLLED_COMMANDER='controlled_commander'


@dataclass(frozen=True,slots=True)
class TargetedSpellCostSpec:
    subject: TargetedCostSubject
    generic: int

    def __post_init__(self):
        if not isinstance(self.subject,TargetedCostSubject):raise ValueError('Target-dependent spell cost subject must be typed')
        if type(self.generic) is not int or self.generic<1:raise ValueError('Target-dependent cost requires a positive fixed generic increase')

    def to_dict(self):return {'schema_version':1,'subject':self.subject.value,'generic':self.generic}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','subject','generic'}
                or type(value['schema_version']) is not int or value['schema_version']!=1):raise ValueError('Target-dependent spell cost fields are closed')
        return cls(TargetedCostSubject(value['subject']),value['generic'])


@dataclass(frozen=True,slots=True)
class TargetedSpellCostSource:
    source_ref: str
    source_identity: str
    controller: str
    component_id: str
    spec: TargetedSpellCostSpec
    eligible_target_refs: tuple[str,...]

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.source_ref,self.source_identity,self.controller,self.component_id))
                or not isinstance(self.spec,TargetedSpellCostSpec) or type(self.eligible_target_refs) is not tuple
                or any(type(ref) is not str or not ref for ref in self.eligible_target_refs)
                or len(set(self.eligible_target_refs))!=len(self.eligible_target_refs)):
            raise ValueError('Target-dependent cost source requires captured public identities')

    def applies(self,targets):return bool(set(targets).intersection(self.eligible_target_refs))


def validate_targeted_spell_cost_fact(value):
    if (not isinstance(value,Mapping) or set(value)!={'schema_version','generic','sources'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or type(value['generic']) is not int or value['generic']<0 or not isinstance(value['sources'],(list,tuple))):
        raise ValueError('Target-dependent spell cost fact fields are closed')
    components=set();total=0
    for source in value['sources']:
        if (not isinstance(source,Mapping) or set(source)!={'component_id','source_ref','source_identity','controller','generic'}
                or any(type(source[key]) is not str or not source[key] for key in ('component_id','source_ref','source_identity','controller'))
                or type(source['generic']) is not int or source['generic']<1 or source['component_id'] in components):
            raise ValueError('Target-dependent spell cost repeats or loses its captured source')
        components.add(source['component_id']);total+=source['generic']
    if value['generic']!=total:raise ValueError('Target-dependent spell cost differs from its source total')
    return total
