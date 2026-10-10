from __future__ import annotations

"""Read-only prospective face views shared by cast offers and cost legality."""

from dataclasses import replace


def prospective_cast_card(host,card,face_name=None):
    record=host.card_record(card)
    if record is None or not record.faces:return card
    face=host._select_cast_face(record,face_name)
    return replace(card,active_face=str(face['name']),annotations={**card.annotations,'prospective_cast_face':True})


def prospective_cast_record(record,face):
    return replace(record,type_line=str(face.get('type_line') or ''),mana_cost=str(face.get('mana_cost') or '')) if face else record


def cast_resolution_is_trusted(host,record,face,program):
    return host.semantic_program_is_current_trusted(program) if program is not None else host._trusted_generic_spell(prospective_cast_record(record,face))


def selected_cast_program(host,request,record,face,method_spec):
    from .proposal import _spell_semantic_key
    from .model import CastProposalError
    key=f'builtin:{request.cast_method}-face-down' if method_spec is not None else _spell_semantic_key(record,face)
    program=None if method_spec is not None else host.semantics.get(key)
    if method_spec is None and host.state.config.semantic_policy=='trusted_only' and not cast_resolution_is_trusted(host,record,face,program):
        raise CastProposalError('This selected face lacks a trusted resolution contract',status='unresolved',reason='semantic_policy_requires_trusted')
    return key,program


def offered_cast_methods(host,card):
    from ...morph import FACE_DOWN_CAST_METHODS
    record=host.card_record(card)
    faces=tuple(str(f['name']) for f in record.faces) if record is not None and record.layout in {'split','modal_dfc'} else (None,)
    return tuple((None,name) for name in faces)+tuple((method,None) for method in FACE_DOWN_CAST_METHODS)


def candidate_zone_cast_permission(host,seat,card):
    if host._compiled_zone_cast_permission(seat,card):return True
    return any(host._compiled_zone_cast_permission(seat,prospective_cast_card(host,card,name)) for method,name in offered_cast_methods(host,card) if method is None and name is not None)


def cast_offer_id(card,record,face,cast_method=None):
    if cast_method:return f'cast-{cast_method}:{card.ref}'
    if face is None or not record.faces or str(face.get('name'))==str(record.faces[0].get('name')):return f'cast:{card.ref}'
    return f"cast:{card.ref}:face:{face['name']}"
