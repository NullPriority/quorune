"""Explicit fixed creature-subtype permanent queries for private hand entry."""
from collections.abc import Mapping
import re
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec

PERMANENT_SUBTYPE_HAND_KIND='permanent_subtype_hand_query'
PERMANENT_SUBTYPE_HAND_CAPABILITY='zone.move.private_hand_permanent_subtype'


def permanent_subtype_hand_descriptor(subject):
    match=re.fullmatch(r'(?P<subtype>.+?) permanent',' '.join(subject.split()),re.I)
    if match is None:return None
    subtype=canonical_creature_subtype(match['subtype'])
    if subtype is None:return None
    return {'kind':PERMANENT_SUBTYPE_HAND_KIND,'schema_version':1,'subtype':subtype}


def decode_permanent_subtype_hand_query(value):
    from ..hand_entry_queries import PERMANENT_TYPES
    if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','subtype'}
            or value.get('kind')!=PERMANENT_SUBTYPE_HAND_KIND
            or type(value.get('schema_version')) is not int or value['schema_version']!=1
            or type(value.get('subtype')) is not str
            or canonical_creature_subtype(value['subtype'])!=value['subtype']):
        raise ValueError('Permanent subtype hand query requires one canonical subtype and explicit version')
    return ObjectQuerySpec(zones=('hand',),types_any=PERMANENT_TYPES,subtypes_all=(value['subtype'],))


def permanent_subtype_hand_runtime_dependencies(program):
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            if value.get('kind')==PERMANENT_SUBTYPE_HAND_KIND:
                decode_permanent_subtype_hand_query(value)
                required.update((PERMANENT_SUBTYPE_HAND_CAPABILITY,'zone.move.fixed_private_hand_choice'))
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))
