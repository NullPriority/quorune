from __future__ import annotations

"""Read exact current object and copiable facts without merge authority."""

from typing import Any,Mapping,Protocol

from ..composition_snapshot import CompositionObjectSnapshot
from ..continuous_effect_model import ContinuousObjectIdentity,Layer
from ..entry_copy import COPIABLE_ORACLE_FIELD,COPIABLE_FACE_FIELD,copiable_oracle_id
from ..model import CardInstance
from ..permanent_composition import _validate_identity
from ..card_program_faces import selected_face_id


class CompositionReadPort(Protocol):
    state: Any

    def _copyable_characteristics(self,card:CardInstance)->Mapping[str,Any]: ...
    def _effective_card_data(self,card:CardInstance,*,maximum_layer:Layer)->Mapping[str,Any]: ...
    def card_record(self,card:CardInstance)->Any: ...


def capture_composition_object(host:CompositionReadPort,identity:ContinuousObjectIdentity,*,
        expected_zone:str,expected_stack_ref:str|None=None)->CompositionObjectSnapshot:
    _validate_identity(identity)
    if (expected_zone not in ('battlefield','stack')
            or expected_zone=='battlefield' and expected_stack_ref is not None
            or expected_zone=='stack' and (type(expected_stack_ref) is not str or not expected_stack_ref)):
        raise ValueError('Composition capture requires an explicit zone and exact stack reference when applicable')
    card=host.state.cards.get(identity.object_id)
    if (card is None or card.logical_object_id!=identity.logical_object_id
            or card.zone!=expected_zone
            or card.owner not in host.state.players or card.controller not in host.state.players):
        raise ValueError('Composition capture requires its exact current object and known physical owner/controller')
    controller=card.controller;stack_ref=None;stack_kind=None
    if card.zone=='battlefield':
        memberships=tuple(seat for seat,player in host.state.players.items()
            for object_id in player.zones.get('battlefield',()) if object_id==card.object_id)
        if memberships!=(controller,):raise ValueError('Composition capture requires unique current battlefield membership')
    elif card.zone=='stack':
        spells=tuple(item for item in host.state.stack
            if item.card_object_id==card.object_id and item.kind in ('spell','spell_copy'))
        if len(spells)!=1 or spells[0].ref!=expected_stack_ref:
            raise ValueError('Composition capture requires one exact current stack spell')
        spell=spells[0];controller=spell.controller;stack_ref=spell.ref;stack_kind=spell.kind
        if controller not in host.state.players:raise ValueError('Composition spell controller is unknown')
    else:raise ValueError('Composition capture requires a battlefield object or stack spell')
    if card.face_down:
        values=dict(host._effective_card_data(card,maximum_layer=Layer.COPY))
        values['activated_abilities']=[]
        values[COPIABLE_ORACLE_FIELD]='custom-copy:face-down';values[COPIABLE_FACE_FIELD]='front'
    else:
        values=dict(host._copyable_characteristics(card))
        values.setdefault(COPIABLE_ORACLE_FIELD,copiable_oracle_id(card,values))
        record=host.card_record(card)
        values.setdefault(COPIABLE_FACE_FIELD,selected_face_id(record,card) if record is not None else 'front')
    return CompositionObjectSnapshot.capture(card,copiable_values=values,controller=controller,
        stack_ref=stack_ref,stack_kind=stack_kind)
