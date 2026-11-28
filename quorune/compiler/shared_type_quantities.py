"""Whole-sentence shared-creature-type counts over current recipients."""
from __future__ import annotations

from . import regex_working_set as re

from ..object_predicate import ObjectQuerySpec
from ..recipient_modifier_model import RecipientQuantityKind, RecipientQuantitySpec, RECIPIENT_SHARED_NAME_CAPABILITY, RECIPIENT_SHARED_TYPE_CAPABILITY
from ..semantic_runtime.shared_type_anthems import (SharedNameAnthemHandler, SharedTypeAnthemHandler, SHARED_NAME_ANTHEM_HANDLER, SHARED_TYPE_ANTHEM_HANDLER)


_SHARED_TYPE_MODIFIER = re.compile(
    r'^(?P<subject>Each creature|Enchanted creature|Equipped creature) gets '
    r'(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) for each other creature '
    r'(?P<domain>on the battlefield|you control) that shares (?:a|at least one) creature type with it\.?$',re.I)


def _same_name_creature_anthem(text):
    match = re.fullmatch(
        r'Each nontoken creature you control gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) '
        r'for each other creature you control with the same name as that creature[.]?', text.strip(), re.I,
    )
    if match is None:
        return None
    descriptor = {'handler_id': SHARED_NAME_ANTHEM_HANDLER, 'schema_version': 1, 'event': 'characteristics.evaluate',
                  'condition': {'target_controller': 'source_controller', 'exclude_source': False,
                                'predicate': ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), token=False).to_dict()},
                  'modifier': {'power': int(match['power']), 'toughness': int(match['toughness'])}}
    try:
        SharedNameAnthemHandler().validate(descriptor)
    except (TypeError, ValueError):
        return None
    return 'continuous-recipient-shared-creature-name-v1', descriptor, (RECIPIENT_SHARED_NAME_CAPABILITY,)


def shared_type_quantity_handler(text):
    same_name = _same_name_creature_anthem(text)
    if same_name is not None:
        return same_name
    match=_SHARED_TYPE_MODIFIER.fullmatch(text.strip())
    if match is None:
        return None
    quantity=RecipientQuantitySpec(RecipientQuantityKind.SHARED_CREATURE_TYPES,schema_version=2,
        controller_relation='source_controller' if match['domain'].casefold()=='you control' else 'any')
    descriptor={'handler_id':SHARED_TYPE_ANTHEM_HANDLER,'schema_version':1,'event':'characteristics.evaluate',
        'condition':{'target_controller':'any','predicate':ObjectQuerySpec(zones=('battlefield',),types_all=('creature',)).to_dict(),
                     'exclude_source':False},
        'relation':'none' if match['subject'].casefold()=='each creature' else 'source_attached_to_object',
        'modifier':{'schema_version':1,'quantity':quantity.to_dict(),'power':int(match['power']),'toughness':int(match['toughness'])}}
    try:SharedTypeAnthemHandler().validate(descriptor)
    except (TypeError,ValueError):return None
    return 'continuous-recipient-shared-creature-type-v1',descriptor,(RECIPIENT_SHARED_TYPE_CAPABILITY,)
