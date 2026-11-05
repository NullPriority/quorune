from __future__ import annotations

"""Read-only physical face preparation for one retained composition."""

from dataclasses import dataclass,replace

from ..composition_storage import PermanentCompositionStorage
from ..continuous_effect_model import ContinuousObjectIdentity
from ..defined_token_faces import defined_token_definition
from ..model import CardInstance
from ..permanent_composition import _validate_identity
from ..replacement.immutable import FrozenMap,thaw_value


def _retained_root(identity,root_state,before,after):
    if (not isinstance(identity,ContinuousObjectIdentity) or not isinstance(root_state,FrozenMap)
            or not isinstance(before,PermanentCompositionStorage) or not isinstance(after,PermanentCompositionStorage)
            or before.topology!=after.topology or before.merges!=after.merges
            or identity!=before.topology.object_identity):
        raise ValueError('Physical face preparation requires its exact retained topology')
    root=CardInstance.from_dict(thaw_value(root_state))
    if FrozenMap(root.to_dict())!=root_state or root.permanent_composition!=before:
        raise ValueError('Physical face preparation does not match its current root')
    before.validate_carrier(root)
    if root.face_down!=before.parts[0].card_state['face_down']:
        raise ValueError('A retained composition has the face status of its top component')
    return root


@dataclass(frozen=True,slots=True)
class PreparedCompositionTransform:
    object_identity: ContinuousObjectIdentity
    root_state: FrozenMap
    before: PermanentCompositionStorage
    after: PermanentCompositionStorage
    changed_refs: tuple[str,...]

    def __post_init__(self):
        _retained_root(self.object_identity,self.root_state,self.before,self.after)
        if (not isinstance(self.changed_refs,tuple) or not self.changed_refs
                or any(type(value) is not str or not value for value in self.changed_refs)
                or len(self.changed_refs)!=len(set(self.changed_refs))):
            raise ValueError('A composition transform requires its exact retained topology and changed physical faces')
        actual=tuple(first.card_state['ref'] for first,second in zip(self.before.parts,self.after.parts) if first!=second)
        if actual!=self.changed_refs:raise ValueError('Composition transform changes do not match its physical snapshots')
        for first,second in zip(self.before.parts,self.after.parts):
            if first==second:continue
            original=thaw_value(first.card_state);updated=thaw_value(second.card_state)
            previous=original.pop('active_face',None);current=updated.pop('active_face',None)
            if (original!=updated or type(current) is not str or not current or current==previous
                    or replace(second,card_state=first.card_state,copiable_values=first.copiable_values)!=first):
                raise ValueError('A physical transform changes only its face and derived copiable values')


def _component_characteristic_view(card,*,is_token):
    if is_token and card.object_kind in {'card_copy','spell_copy'}:
        return replace(card,is_token=True,object_kind='token')
    return card


def _next_physical_face(host,card,*,is_token):
    if card.face_down:return None
    definition=defined_token_definition(_component_characteristic_view(card,is_token=is_token))
    if is_token:
        if definition is None:
            if card.object_kind in {'card_copy','spell_copy'}:
                record=host.card_db.by_oracle_id(card.oracle_id)
                if record is not None and len(record.faces)>1:
                    raise ValueError('Copied-spell double faces require their explicit physical token definition')
            return None
        current=definition.current_face(card.active_face)
        other=definition.other_face(current.name)
        if any({'instant','sorcery'}.intersection(host._type_parts(face.type_line)[0])
                for face in (definition.front,definition.back)):return None
        return other.name
    record=host.card_db.by_oracle_id(card.oracle_id)
    if record is None or record.layout not in {'transform','modal_dfc'} or len(record.faces)!=2:return None
    names=tuple(str(face.get('name') or '') for face in record.faces)
    current=card.active_face or names[0]
    if not all(names) or len(set(names))!=2 or current not in names:
        raise ValueError('Stored physical face does not match the pinned card definition')
    index=1 if current==names[0] else 0
    if {'instant','sorcery'}.intersection(host._type_parts(str(record.faces[index].get('type_line') or ''))[0]):return None
    return names[index]


def prepare_composition_transform(host,identity,*,expected_transform_count):
    _validate_identity(identity)
    if type(expected_transform_count) is not int or expected_transform_count<0:
        raise ValueError('A composition transform requires its source count snapshot')
    root=host.state.cards.get(identity.object_id)
    if (root is None or root.logical_object_id!=identity.logical_object_id or root.zone!='battlefield'
            or root.permanent_composition is None):
        raise ValueError('A composition transform requires its retained current battlefield carrier')
    if root.phased_out or root.face_down or root.transform_count!=expected_transform_count:return None
    storage=root.permanent_composition;storage.validate_carrier(root)
    updated=[];changed=[]
    for snapshot,component in zip(storage.parts,storage.topology.components):
        card=snapshot.detached_card()
        face=_next_physical_face(host,card,is_token=component.is_token)
        if face is None:updated.append(snapshot);continue
        card.active_face=face
        values=host._copyable_characteristics(_component_characteristic_view(card,is_token=component.is_token))
        updated.append(replace(snapshot,card_state=FrozenMap(card.to_dict()),copiable_values=FrozenMap(values)))
        changed.append(card.ref)
    if not changed:return None
    return PreparedCompositionTransform(identity,FrozenMap(root.to_dict()),storage,
        replace(storage,parts=tuple(updated)),tuple(changed))


def _turned_up_snapshot_card(snapshot,viewers):
    from ..zone_object_state import turn_card_face_up
    original=snapshot.detached_card()
    card=replace(original,zone='battlefield')
    turn_card_face_up(card,viewers=viewers)
    return replace(card,zone=original.zone)


@dataclass(frozen=True,slots=True)
class PreparedCompositionFaceUp:
    object_identity: ContinuousObjectIdentity
    root_state: FrozenMap
    before: PermanentCompositionStorage
    after: PermanentCompositionStorage
    viewers: tuple[str,...]
    blocked_refs: tuple[str,...]=()

    def __post_init__(self):
        root=_retained_root(self.object_identity,self.root_state,self.before,self.after)
        if (not root.face_down or not isinstance(self.viewers,tuple) or not self.viewers
                or any(type(value) is not str or not value for value in self.viewers)
                or len(self.viewers)!=len(set(self.viewers))
                or not isinstance(self.blocked_refs,tuple)
                or any(type(value) is not str for value in self.blocked_refs)
                or len(self.blocked_refs)!=len(set(self.blocked_refs))
                or set(self.blocked_refs)-{part.card_state['ref'] for part in self.before.parts}):
            raise ValueError('Face-up preparation requires its face-down root, viewers and physical refusal scope')
        if self.blocked_refs:
            if self.after!=self.before:raise ValueError('A refused turn-up cannot change any physical part')
            return
        for first,second in zip(self.before.parts,self.after.parts):
            if not first.card_state['face_down']:
                if first!=second:raise ValueError('Turning up cannot change an already face-up component')
                continue
            expected=_turned_up_snapshot_card(first,self.viewers)
            expected.active_face=second.card_state['active_face']
            if (FrozenMap(expected.to_dict())!=second.card_state
                    or replace(second,card_state=first.card_state,copiable_values=first.copiable_values)!=first):
                raise ValueError('Turning up changes only face status, reveal metadata and derived copiable values')

    @property
    def can_turn_face_up(self):return not self.blocked_refs


def _physical_turn_up_face(host,card,*,is_token):
    if is_token:
        if not card.face_down:return card.active_face,''
        definition=defined_token_definition(_component_characteristic_view(card,is_token=True))
        if definition is None:
            if not card.is_token:raise ValueError('Turning up a captured copied spell requires its represented physical creation faces')
            return card.active_face,''
        return definition.front.name,definition.front.type_line
    record=host.card_db.by_oracle_id(card.oracle_id)
    if record is None:raise ValueError('Turn-up physical definition is absent from the pinned database')
    if record.layout in {'transform','modal_dfc'}:
        if len(record.faces)!=2:raise ValueError('Turn-up double faces require their pinned front face')
        front=record.faces[0]
        if not front.get('name') or not front.get('type_line'):
            raise ValueError('Turn-up front-face identity and types must be pinned')
        return str(front['name']),str(front['type_line'])
    if record.layout=='flip':raise ValueError('Flip components require their represented retained flipped status')
    return card.active_face,record.type_line


def prepare_composition_face_up(host,identity):
    _validate_identity(identity)
    root=host.state.cards.get(identity.object_id)
    if (root is None or root.logical_object_id!=identity.logical_object_id or root.zone!='battlefield'
            or root.permanent_composition is None):
        raise ValueError('Face-up preparation requires its retained current battlefield carrier')
    if root.phased_out or not root.face_down:return None
    storage=root.permanent_composition;storage.validate_carrier(root)
    viewers=tuple(host.seats)
    physical=tuple(_physical_turn_up_face(host,snapshot.detached_card(),is_token=component.is_token)
        for snapshot,component in zip(storage.parts,storage.topology.components))
    blocked=tuple(snapshot.card_state['ref'] for snapshot,component,(_face,type_line)
        in zip(storage.parts,storage.topology.components,physical)
        if not component.is_token and {'instant','sorcery'}.intersection(host._type_parts(type_line)[0]))
    if blocked:return PreparedCompositionFaceUp(identity,FrozenMap(root.to_dict()),storage,storage,viewers,blocked)
    updated=[]
    for snapshot,component,(face,_type_line) in zip(storage.parts,storage.topology.components,physical):
        if not snapshot.card_state['face_down']:updated.append(snapshot);continue
        card=_turned_up_snapshot_card(snapshot,viewers);card.active_face=face
        updated.append(replace(snapshot,card_state=FrozenMap(card.to_dict()),
            copiable_values=FrozenMap(host._copyable_characteristics(
                _component_characteristic_view(card,is_token=component.is_token)))))
    return PreparedCompositionFaceUp(identity,FrozenMap(root.to_dict()),storage,
        replace(storage,parts=tuple(updated)),viewers)
