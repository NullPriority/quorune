from __future__ import annotations

"""Closed physical storage for one retained composed permanent."""

from collections.abc import Mapping
from dataclasses import dataclass

from .composition_snapshot import CompositionObjectSnapshot
from .permanent_composition import PermanentComponent,PermanentComposition,MergePosition


def _component(snapshot):
    raw=snapshot.card_state
    copied=snapshot.stack_kind=='spell_copy' or raw['object_kind'] in ('spell_copy','card_copy')
    token=raw['is_token'] or copied
    designation=raw.get('commander_designation_id')
    if raw['is_commander'] and (designation is None or token):
        raise ValueError('Stored physical commanders require their unambiguous card designation')
    return PermanentComponent(snapshot.identity,raw['owner'],token,designation)


@dataclass(frozen=True,slots=True)
class CompositionMergeStep:
    component_object_id: str
    position: MergePosition
    timestamp: int

    def __post_init__(self):
        if (type(self.component_object_id) is not str or not self.component_object_id
                or not isinstance(self.position,MergePosition) or type(self.timestamp) is not int or self.timestamp<=0):
            raise ValueError('A merge step requires its physical part, position and timestamp')

    def to_dict(self):
        return {'component_object_id':self.component_object_id,'position':self.position.value,'timestamp':self.timestamp}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'component_object_id','position','timestamp'}:
            raise ValueError('Merge steps have a closed wire shape')
        return cls(value['component_object_id'],MergePosition(value['position']),value['timestamp'])


@dataclass(frozen=True,slots=True)
class PermanentCompositionStorage:
    topology: PermanentComposition
    parts: tuple[CompositionObjectSnapshot,...]
    merges: tuple[CompositionMergeStep,...]=()
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.topology,PermanentComposition)
                or not isinstance(self.parts,(list,tuple)) or not isinstance(self.merges,(list,tuple))
                or any(not isinstance(part,CompositionObjectSnapshot) for part in self.parts)
                or any(not isinstance(step,CompositionMergeStep) for step in self.merges)):
            raise ValueError('Composition storage requires its closed version and typed topology, parts and steps')
        parts=tuple(self.parts);merges=tuple(self.merges)
        references=tuple(part.card_state['ref'] for part in parts)
        if len(references)!=len(set(references)):raise ValueError('Stored physical references must be unique')
        if tuple(_component(part) for part in parts)!=self.topology.components:
            raise ValueError('Stored physical snapshots do not match the ordered topology')
        ids=tuple(step.component_object_id for step in merges)
        if (len(merges)!=self.topology.mutation_count or len(ids)!=len(set(ids))
                or set(ids)-set(self.topology.physical_object_ids)
                or any(first.timestamp>=second.timestamp for first,second in zip(merges,merges[1:]))):
            raise ValueError('Stored merge history is inconsistent')
        by_id={component.identity.object_id:component for component in self.topology.components}
        reconstructed=tuple(component for component in self.topology.components if component.identity.object_id not in ids)
        if not reconstructed:raise ValueError('A retained composition requires an original physical base')
        for step in merges:
            component=by_id[step.component_object_id]
            reconstructed=(component,*reconstructed) if step.position is MergePosition.TOP else (*reconstructed,component)
        if reconstructed!=self.topology.components:raise ValueError('Stored step order contradicts physical order')
        object.__setattr__(self,'parts',parts);object.__setattr__(self,'merges',merges)

    @classmethod
    def single(cls,snapshot):
        if not isinstance(snapshot,CompositionObjectSnapshot) or snapshot.card_state['zone']!='battlefield':
            raise ValueError('Composition storage begins with one current battlefield snapshot')
        return cls(PermanentComposition.single(_component(snapshot)),(snapshot,))

    def merge(self,incoming,*,position,timestamp,expected_object):
        if not isinstance(incoming,CompositionObjectSnapshot) or incoming.card_state['zone']!='stack':
            raise ValueError('A composition part must come from its exact resolving stack snapshot')
        step=CompositionMergeStep(incoming.identity.object_id,position,timestamp)
        topology=self.topology.merge(_component(incoming),position=position,expected_object=expected_object)
        parts=(incoming,*self.parts) if position is MergePosition.TOP else (*self.parts,incoming)
        return PermanentCompositionStorage(topology,parts,(*self.merges,step),self.schema_version)

    def validate_carrier(self,card):
        if (card.zone!='battlefield' or card.object_id!=self.topology.object_identity.object_id
                or card.logical_object_id!=self.topology.object_identity.logical_object_id
                or card.owner!=self.topology.owner or card.is_token!=self.topology.is_token
                or card.object_kind!=('token' if self.topology.is_token else 'card')
                or card.is_commander!=self.topology.is_commander
                or card.commander_designation_id is not None
                    and card.commander_designation_id not in self.topology.commander_designation_ids):
            raise ValueError('Composition storage does not belong to this current battlefield object')
        physical_root=next((part for part in self.parts if part.identity.object_id==card.object_id),None)
        if physical_root is not None and physical_root.card_state['ref']!=card.ref:
            raise ValueError('The retained physical root reference changed')

    def to_dict(self):
        return {'schema_version':self.schema_version,'topology':self.topology.to_dict(),
            'parts':[part.to_dict() for part in self.parts],'merges':[step.to_dict() for step in self.merges]}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','topology','parts','merges'}
                or not isinstance(value['parts'],(list,tuple)) or not isinstance(value['merges'],(list,tuple))):
            raise ValueError('Composition storage has a closed wire shape')
        return cls(PermanentComposition.from_dict(value['topology']),
            tuple(CompositionObjectSnapshot.from_dict(part) for part in value['parts']),
            tuple(CompositionMergeStep.from_dict(step) for step in value['merges']),value['schema_version'])


def validate_composition_membership(state):
    physical_ids=set();physical_refs=set()
    live_refs={card.ref:card.object_id for card in state.cards.values()}
    for carrier in state.cards.values():
        storage=carrier.permanent_composition
        if storage is None:continue
        storage.validate_carrier(carrier)
        for part in storage.parts:
            object_id=part.identity.object_id;reference=part.card_state['ref']
            if (object_id in physical_ids or reference in physical_refs
                    or object_id!=carrier.object_id and object_id in state.cards
                    or reference in live_refs and live_refs[reference]!=carrier.object_id):
                raise ValueError('A stored physical part is duplicated as a live or stored object')
            physical_ids.add(object_id);physical_refs.add(reference)
