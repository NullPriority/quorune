from __future__ import annotations

"""Intrinsic Adventure resolution and one zone-bound normal-face permission."""

from collections.abc import Mapping
from .cast_face_offers import complete_cast_face_offers
from .characteristic_evaluation import type_parts

ADVENTURE_CONTEXT='adventure_resolution'
ADVENTURE_PERMISSION='adventure_cast_permission'
ADVENTURE_CAPABILITY='casting.lifecycle.adventure'
ADVENTURE_COVERAGE='intrinsic_adventure_resolution_required'


def _context(value):
    if (not isinstance(value,Mapping) or set(value)!={'schema_version','adventure_face','normal_face'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or any(type(value[key]) is not str or not value[key] for key in ('adventure_face','normal_face'))
            or value['adventure_face']==value['normal_face']):
        raise ValueError('Adventure resolution requires its closed alternative-face binding')
    return value


def adventure_stack_context(state,record,card):
    if not complete_cast_face_offers(state) or record.layout!='adventure':return {}
    selected=next((face for face in record.faces if face.get('name')==card.active_face),None)
    if selected is None or 'adventure' not in type_parts(str(selected.get('type_line') or ''))[1]:return {}
    normal=[face for face in record.faces if 'adventure' not in type_parts(str(face.get('type_line') or ''))[1]]
    if len(normal)!=1:raise ValueError('Adventure must identify one normal face')
    return {ADVENTURE_CONTEXT:_context(dict(schema_version=1,adventure_face=str(selected['name']),normal_face=str(normal[0]['name'])))}


def adventure_resolution_destination(item,destination):
    raw=item.context.get(ADVENTURE_CONTEXT)
    if raw is None:return destination
    binding=_context(raw)
    if item.kind not in {'spell','spell_copy'} or item.chosen_face!=binding['adventure_face']:
        raise ValueError('Adventure resolution differs from its chosen spell face')
    return 'exile' if destination in {None,'graveyard'} else destination


def complete_adventure_resolution(item,card):
    raw=item.context.get(ADVENTURE_CONTEXT)
    if raw is None:return
    binding=_context(raw)
    if (item.kind!='spell' or card.object_kind!='card' or card.zone!='exile'):
        return
    card.annotations[ADVENTURE_PERMISSION]=dict(schema_version=1,player=item.controller,
        object_id=card.object_id,logical_object_id=card.logical_object_id,normal_face=binding['normal_face'])


def adventure_cast_permission(host,seat,card):
    raw=card.annotations.get(ADVENTURE_PERMISSION)
    if raw is None:return False
    if (not isinstance(raw,Mapping) or set(raw)!={'schema_version','player','object_id','logical_object_id','normal_face'}
            or type(raw['schema_version']) is not int or raw['schema_version']!=1
            or any(type(raw[key]) is not str or not raw[key] for key in ('player','object_id','logical_object_id','normal_face'))):
        raise ValueError('Adventure permission has malformed zone-bound identity')
    if (card.zone!='exile' or card.object_id!=raw['object_id'] or card.logical_object_id!=raw['logical_object_id']
            or seat!=raw['player']):return False
    record=host.card_record(card)
    if record is None or record.layout!='adventure':return False
    face=card.active_face or str(record.faces[0]['name'])
    return face==raw['normal_face']
