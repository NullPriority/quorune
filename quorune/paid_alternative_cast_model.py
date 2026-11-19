"""Versioned sealed payment facts; Kicker and unmarked casts keep their contracts."""
from collections.abc import Mapping
from dataclasses import dataclass
from .continuous_effect_model import ContinuousObjectIdentity

PAID_ALTERNATIVE_CAST_FIELD = 'paid_alternative_cast'
PAID_ALTERNATIVE_CAST_CAPABILITY = 'casting.fact.fixed_alternative_paid'
PAID_ALTERNATIVE_CAST_MECHANIC = 'paid-alternative-cast-results'
PAID_ALTERNATIVE_RESULT_VERSION = 18
PAID_ALTERNATIVE_ENTRY_FIELD = 'paid_alternative_entry_information'
PAID_ALTERNATIVE_ENTRY_CONDITION = 'paid_alternative_cast_method'


def _method(method):
    if type(method) is not str or method != 'surge':
        raise ValueError('Unrepresented paid alternative casting method')


@dataclass(frozen=True, slots=True)
class PaidAlternativeCastRequirement:
    method: str
    schema_version: int = 1

    def __post_init__(self):
        _method(self.method)
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported paid alternative requirement version')

    def to_dict(self):
        return {'kind':'paid_alternative_cast_requirement','schema_version':1,'method':self.method}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','method'}
                or value['kind']!='paid_alternative_cast_requirement'):
            raise ValueError('Paid alternative requirement fields are closed')
        return cls(value['method'],value['schema_version'])


@dataclass(frozen=True, slots=True)
class PaidAlternativeCastFact:
    method: str
    paid: bool
    schema_version: int = 1

    def __post_init__(self):
        _method(self.method)
        if type(self.paid) is not bool or type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Paid alternative fact needs its exact version and boolean')

    def to_dict(self):
        return {'kind':'paid_alternative_cast_fact','schema_version':1,'method':self.method,'paid':self.paid}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','method','paid'}
                or value['kind']!='paid_alternative_cast_fact'):
            raise ValueError('Paid alternative fact fields are closed')
        return cls(value['method'],value['paid'],value['schema_version'])

    def satisfies(self,requirement):
        if not isinstance(requirement,PaidAlternativeCastRequirement):
            raise ValueError('Paid alternative condition requires its typed request')
        return self.method == requirement.method and self.paid


@dataclass(frozen=True, slots=True)
class PaidAlternativeEntryInformation:
    source: ContinuousObjectIdentity
    fact: PaidAlternativeCastFact
    stack_logical_object_id: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.source,ContinuousObjectIdentity) or not isinstance(self.fact,PaidAlternativeCastFact)
                or type(self.source.object_id) is not str or not self.source.object_id
                or type(self.source.logical_object_id) is not str
                or not self.source.logical_object_id.startswith(self.source.object_id+'@')
                or not self.source.logical_object_id.rsplit('@',1)[-1].isdigit()):
            raise ValueError('Paid entry requires its exact new source incarnation and typed fact')
        stack=self.stack_logical_object_id
        if (stack is not None and (type(stack) is not str or not stack.startswith(self.source.object_id+'@')
                or not stack.rsplit('@',1)[-1].isdigit()) or self.fact.paid and stack is None):
            raise ValueError('A paid entry requires the original resolving stack incarnation')

    def to_dict(self):
        return {'kind':'paid_alternative_entry_information','schema_version':1,
            'source':{'object_id':self.source.object_id,'logical_object_id':self.source.logical_object_id},
            'fact':self.fact.to_dict(),'stack_logical_object_id':self.stack_logical_object_id}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping)
                or set(value)!={'kind','schema_version','source','fact','stack_logical_object_id'}
                or value['kind']!='paid_alternative_entry_information' or not isinstance(value['source'],Mapping)
                or set(value['source'])!={'object_id','logical_object_id'}):
            raise ValueError('Paid entry information fields are closed')
        return cls(ContinuousObjectIdentity(**dict(value['source'])),PaidAlternativeCastFact.from_dict(value['fact']),
            value['stack_logical_object_id'],value['schema_version'])


def validate_paid_alternative_instruction(effect,*,allow_bound=False):
    fields={'op','schema_version','player','condition','cast_fact','effects','mechanic_ids','prefix_mechanic_ids'}
    if (not isinstance(effect,Mapping) or set(effect)!=fields or effect['op']!="apply_if_public_condition"
            or type(effect['schema_version']) is not int or effect['schema_version']!=PAID_ALTERNATIVE_RESULT_VERSION
            or type(effect['player']) is not str or not effect['player']):
        raise ValueError('Paid alternative result instruction fields are closed')
    requirement=PaidAlternativeCastRequirement.from_dict(effect['condition'])
    if effect['cast_fact']!="$context.paid_alternative_cast":
        if not allow_bound:raise ValueError('Paid result needs its sealed casting context reference')
        fact=PaidAlternativeCastFact.from_dict(effect['cast_fact'])
        if fact.method!=requirement.method:raise ValueError('Paid result method changed')
    for name in ('mechanic_ids','prefix_mechanic_ids'):
        values=effect[name]
        if (not isinstance(values,(list,tuple)) or any(type(v) is not str or not v for v in values)
                or len(set(values))!=len(values) or PAID_ALTERNATIVE_CAST_MECHANIC in values or 'fixed-resolution-public-condition' in values):
            raise ValueError('Paid alternative result component mechanics changed')
    children=effect['effects']
    if (not isinstance(children,(list,tuple)) or not 1<=len(children)<=8
            or any(not isinstance(c,Mapping) or c.get('op')=="apply_if_public_condition" for c in children)
            or not effect['mechanic_ids']):raise ValueError('Paid result requires bounded fixed child effects')
    from .resolution_conditions import cast_fact_result_is_fixed
    if not cast_fact_result_is_fixed(children,effect['mechanic_ids']):
        raise ValueError('Paid result child quantities or references are unrepresented')
    return requirement,tuple(children)


def validated_paid_entry_occurrence(occurrence):
    value=getattr(occurrence,PAID_ALTERNATIVE_ENTRY_FIELD)
    if value is None:return None
    info=PaidAlternativeEntryInformation.from_dict(value)
    if (occurrence.destination!='battlefield' or info.source!=ContinuousObjectIdentity(occurrence.object_id,occurrence.current_logical_object_id)
            or info.stack_logical_object_id is not None and (occurrence.origin!='stack'
                or info.stack_logical_object_id!=occurrence.previous_logical_object_id)):
        raise ValueError('Paid entry information differs from the actual committed occurrence')
    from .replacement.immutable import FrozenMap
    return FrozenMap(info.to_dict())
