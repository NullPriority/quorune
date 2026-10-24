from __future__ import annotations

"""Bind a plain retained entry designation to a prevention-source predicate."""

from collections.abc import Mapping
from .entry_designations import EntryDesignationKind, validate_designation
from .damage_source import REPRESENTED_DAMAGE_SOURCE_ZONES
from .object_query import ObjectQuerySpec, validate_chosen_damage_source_predicate

DESIGNATION_PREVENTION_CAPABILITY='damage.prevention.source_entry_designation'
DESIGNATION_PREDICATE_KIND='source_entry_designation_predicate'
DESIGNATION_SCOPE_KIND='source_entry_designation_scope'
UNAVAILABLE_DESIGNATION_KIND='unavailable_entry_designation'
DESIGNATION_INFORMATION_FIELD='source_designation_information'


def designation_predicate_kind(value):
    if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','designation'}
            or value.get('kind') not in {DESIGNATION_PREDICATE_KIND,DESIGNATION_SCOPE_KIND}
            or type(value.get('schema_version')) is not int or value['schema_version']!=1):
        raise ValueError('Source designation predicates require their closed version-one schema')
    kind=EntryDesignationKind(value['designation'])
    if value['kind']==DESIGNATION_SCOPE_KIND and kind is not EntryDesignationKind.CREATURE_TYPE:
        raise ValueError('The source designation scope requires its creature type')
    return kind


def designation_predicate_dependencies(value):
    if isinstance(value,Mapping) and value.get('kind') in {DESIGNATION_PREDICATE_KIND,DESIGNATION_SCOPE_KIND}:
        designation_predicate_kind(value)
        return (DESIGNATION_PREVENTION_CAPABILITY,)
    return ()


def _designation_kinds(value):
    if isinstance(value,Mapping):
        if value.get('kind') in {DESIGNATION_PREDICATE_KIND,DESIGNATION_SCOPE_KIND}:return {designation_predicate_kind(value)}
        return set().union(*(_designation_kinds(child) for child in value.values()))
    if isinstance(value,(list,tuple)):
        return set().union(*(_designation_kinds(child) for child in value))
    return set()


def capture_designation_information(source, effects):
    kinds=_designation_kinds(effects)
    if not kinds:return None
    values={kind.value:(validate_designation(kind,source.annotations[kind.annotation])
        if kind.annotation in source.annotations else None) for kind in sorted(kinds,key=lambda k:k.value)}
    return {'schema_version':1,'source_object_id':source.object_id,
        'source_logical_object_id':source.logical_object_id,'values':values}


def resolve_designation_predicate(value,item):
    kind=designation_predicate_kind(value);info=item.context.get(DESIGNATION_INFORMATION_FIELD)
    if (not isinstance(info,Mapping) or set(info)!={'schema_version','source_object_id','source_logical_object_id','values'}
            or type(info.get('schema_version')) is not int or info['schema_version']!=1
            or info['source_object_id']!=(item.source_object_id or item.card_object_id)
            or info['source_logical_object_id']!=item.context.get('source_logical_object_id')
            or not isinstance(info['values'],Mapping) or kind.value not in info['values']):
        raise ValueError('Source designation information is missing or bound to another incarnation')
    for key,choice in info['values'].items():
        declared=EntryDesignationKind(key)
        if choice is not None:validate_designation(declared,choice)
    choice=info['values'][kind.value]
    if choice is None:return {'kind':UNAVAILABLE_DESIGNATION_KIND,'schema_version':1}
    if value['kind']==DESIGNATION_SCOPE_KIND:
        from .damage_modifier_state import DamagePreventionScope
        return DamagePreventionScope(source_characteristics_all=('creature',choice)).to_dict()
    fields={'colors_any':(choice,)} if kind is EntryDesignationKind.COLOR else {'types_all':('creature',),'subtypes_all':(choice,)}
    return validate_chosen_damage_source_predicate(ObjectQuerySpec(
        zones=REPRESENTED_DAMAGE_SOURCE_ZONES,known_to_actor=True,**fields)).to_dict()


def unavailable_designation(value):
    if not isinstance(value,Mapping) or value.get('kind')!=UNAVAILABLE_DESIGNATION_KIND:return False
    if set(value)!={'kind','schema_version'} or type(value.get('schema_version')) is not int or value['schema_version']!=1:
        raise ValueError('Unavailable source designations require their closed version-one result')
    return True
