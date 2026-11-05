from __future__ import annotations

"""Immutable cast receipt and noncopiable original-entry information."""

from collections.abc import Mapping
from dataclasses import dataclass
import re

from .continuous_effect_model import ContinuousObjectIdentity

SOURCE_ENTRY_CAST_INFORMATION_FIELD='source_entry_cast_information'
SOURCE_ENTRY_CAST_CONDITION='source_entry_cast_by_controller'
SOURCE_ENTRY_CAST_COVERAGE='source_entry_cast_intervening'
SOURCE_ENTRY_CAST_CAPABILITY='trigger.condition.source_entry_cast'
SOURCE_ENTRY_CAST_MECHANIC='source-entry-cast-condition'
SOURCE_ENTRY_CAST_HAND_CAPABILITY='trigger.condition.source_entry_cast_hand'
SOURCE_ENTRY_CAST_HAND_COVERAGE='source_entry_cast_hand_intervening'
SOURCE_ENTRY_CAST_HAND_MECHANIC='source-entry-cast-hand-condition'
_CAST_ORIGINS=frozenset({'hand','library','graveyard','exile','command','outside'})


def source_entry_cast_condition(*,origin=None):
    value={'field':SOURCE_ENTRY_CAST_CONDITION,'op':'eq','value':True}
    if origin is not None:value['origin']=origin
    validate_source_entry_cast_condition(value)
    return value


def validate_source_entry_cast_condition(value):
    if (not isinstance(value,Mapping) or set(value) not in ({'field','op','value'},{'field','op','value','origin'})
            or 'origin' in value and value['origin']!='hand'
            or value['field']!=SOURCE_ENTRY_CAST_CONDITION or value['op']!='eq' or value['value'] is not True):
        raise ValueError('Source-entry cast conditions have one closed controller-relative predicate')


def source_entry_cast_program_dependencies(program):
    def mentions(value):
        if isinstance(value,Mapping):return value.get('field')==SOURCE_ENTRY_CAST_CONDITION or any(mentions(child) for child in value.values())
        return isinstance(value,(list,tuple)) and any(mentions(child) for child in value)
    present=mentions(program.event_condition);marker=SOURCE_ENTRY_CAST_COVERAGE in program.coverage
    hand_claim=SOURCE_ENTRY_CAST_HAND_COVERAGE in program.coverage or SOURCE_ENTRY_CAST_HAND_MECHANIC in program.coverage
    if not present and not marker and SOURCE_ENTRY_CAST_MECHANIC not in program.coverage and not hand_claim:return ()
    validate_source_entry_cast_condition(program.event_condition)
    if (not marker or 'intervening_condition' not in program.coverage or program.event!='permanent.enter.self'
            or program.active_zone!='battlefield'):
        raise ValueError('Source-entry cast information requires its complete self-entry/intervening contract')
    hand=program.event_condition.get('origin')=='hand'
    if hand!=(SOURCE_ENTRY_CAST_HAND_COVERAGE in program.coverage) or hand_claim and not hand:
        raise ValueError('A hand-origin cast condition requires its explicit predicate and coverage')
    return (SOURCE_ENTRY_CAST_CAPABILITY,SOURCE_ENTRY_CAST_HAND_CAPABILITY) if hand else (SOURCE_ENTRY_CAST_CAPABILITY,)


def source_entry_cast_runtime_coverage(condition):
    if not isinstance(condition,Mapping) or condition.get('field')!=SOURCE_ENTRY_CAST_CONDITION:return ()
    validate_source_entry_cast_condition(condition)
    return ('intervening_condition',SOURCE_ENTRY_CAST_COVERAGE,*((SOURCE_ENTRY_CAST_HAND_COVERAGE,) if condition.get('origin')=='hand' else ()))


def _text(value):
    return type(value) is str and bool(value) and value.strip()==value


def _logical_identity(value):
    return _text(value) and re.fullmatch(r'.+@(?:0|[1-9][0-9]*)',value) is not None


@dataclass(frozen=True,slots=True)
class CastSpellReceipt:
    caster: str
    origin: str
    origin_owner: str
    stack_ref: str
    stack_logical_object_id: str
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not all(_text(value) for value in
                    (self.caster,self.origin,self.origin_owner,self.stack_ref))
                or self.origin not in _CAST_ORIGINS or not _logical_identity(self.stack_logical_object_id)):
            raise ValueError('A cast receipt requires its caster, origin, owner and exact stack incarnation')

    def to_dict(self):
        return {'schema_version':self.schema_version,'caster':self.caster,'origin':self.origin,
            'origin_owner':self.origin_owner,'stack_ref':self.stack_ref,
            'stack_logical_object_id':self.stack_logical_object_id}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','caster','origin','origin_owner',
                'stack_ref','stack_logical_object_id'}:
            raise ValueError('A cast receipt has a closed versioned wire shape')
        return cls(**dict(value))


@dataclass(frozen=True,slots=True)
class EntryCastInformation:
    source: ContinuousObjectIdentity
    cast: CastSpellReceipt | None
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.source,ContinuousObjectIdentity)
                or not _text(self.source.object_id) or not _logical_identity(self.source.logical_object_id)
                or self.source.logical_object_id.rsplit('@',1)[0]!=self.source.object_id
                or self.cast is not None and not isinstance(self.cast,CastSpellReceipt)):
            raise ValueError('Entry information requires its original incarnation and an explicit cast/noncast fact')

    def was_cast_by(self,controller,*,source,origin=None):
        if not _text(controller) or not isinstance(source,ContinuousObjectIdentity) or source!=self.source:
            raise ValueError('A cast condition requires its controller and original entry incarnation')
        if origin not in (None,'hand'):raise ValueError('Only an explicit hand-origin restriction is represented')
        return self.cast is not None and self.cast.caster==controller and (
            origin is None or self.cast.origin==origin and self.cast.origin_owner==controller)

    def to_dict(self):
        return {'schema_version':self.schema_version,'source':{'object_id':self.source.object_id,
            'logical_object_id':self.source.logical_object_id},'cast':self.cast.to_dict() if self.cast else None}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','source','cast'}
                or not isinstance(value['source'],Mapping)
                or set(value['source'])!={'object_id','logical_object_id'}):
            raise ValueError('Entry cast information has a closed versioned wire shape')
        return cls(ContinuousObjectIdentity(**dict(value['source'])),
            CastSpellReceipt.from_dict(value['cast']) if value['cast'] is not None else None,
            value['schema_version'])
