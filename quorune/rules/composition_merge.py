from __future__ import annotations

"""Immutable physical-merge preparation; permission remains with its caller."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..composition_snapshot import CompositionObjectSnapshot
from ..composition_storage import PermanentCompositionStorage
from ..continuous_effect_model import ContinuousObjectIdentity
from ..model import CardInstance
from ..permanent_composition import MergePosition
from ..replacement.immutable import FrozenMap,thaw_value
from .composition_capture import capture_composition_object


@dataclass(frozen=True,slots=True)
class PreparedCompositionMerge:
    root_state: FrozenMap
    prior_storage: PermanentCompositionStorage
    incoming: CompositionObjectSnapshot
    position: MergePosition
    timestamp: int
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.root_state,Mapping) or not isinstance(self.prior_storage,PermanentCompositionStorage)
                or not isinstance(self.incoming,CompositionObjectSnapshot) or not isinstance(self.position,MergePosition)):
            raise ValueError('A physical merge requires its closed typed preparation')
        state=FrozenMap(self.root_state)
        try:root=CardInstance.from_dict(thaw_value(state))
        except (TypeError,ValueError,KeyError) as exc:raise ValueError('Prepared composition root is malformed') from exc
        if (root.zone!='battlefield' or root.object_id!=self.root_identity.object_id
                or root.logical_object_id!=self.root_identity.logical_object_id
                or FrozenMap(root.to_dict())!=state):
            raise ValueError('Prepared composition does not match its retained root')
        if root.permanent_composition is not None:
            if root.permanent_composition!=self.prior_storage:raise ValueError('Prepared prior composition changed')
        elif len(self.prior_storage.parts)!=1 or self.prior_storage.parts[0].card_state!=state:
            raise ValueError('Prepared singleton does not match its physical root')
        self.storage
        object.__setattr__(self,'root_state',state)

    @property
    def root_identity(self):
        return self.prior_storage.topology.object_identity

    @property
    def chooser(self):
        return self.incoming.controller

    @property
    def storage(self):
        return self.prior_storage.merge(self.incoming,position=self.position,timestamp=self.timestamp,
            expected_object=self.root_identity)

    def to_dict(self):
        return {'schema_version':self.schema_version,'root_state':thaw_value(self.root_state),
            'prior_storage':self.prior_storage.to_dict(),'incoming':self.incoming.to_dict(),
            'position':self.position.value,'timestamp':self.timestamp}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','root_state','prior_storage','incoming','position','timestamp'}:
            raise ValueError('Prepared merges have a closed wire shape')
        return cls(value['root_state'],PermanentCompositionStorage.from_dict(value['prior_storage']),
            CompositionObjectSnapshot.from_dict(value['incoming']),MergePosition(value['position']),
            value['timestamp'],value['schema_version'])


def prepare_composition_merge(host,*,target:ContinuousObjectIdentity,source:ContinuousObjectIdentity,
        stack_ref:str,position:MergePosition)->PreparedCompositionMerge:
    root=host.state.cards.get(target.object_id)
    if root is None or root.zone!='battlefield' or root.logical_object_id!=target.logical_object_id:
        raise ValueError('A physical merge requires its current battlefield target')
    members=tuple(seat for seat,player in host.state.players.items()
        for object_id in player.zones.get('battlefield',()) if object_id==root.object_id)
    if members!=(root.controller,):raise ValueError('Physical merge target membership is ambiguous')
    types,subtypes,_=host._type_parts(str(host._effective_card_data(root).get('type_line') or ''))
    if 'creature' not in types or 'human' in subtypes:raise ValueError('Physical merge target must be a non-Human creature')
    incoming=capture_composition_object(host,source,expected_zone='stack',expected_stack_ref=stack_ref)
    if root.permanent_composition is None:
        prior=PermanentCompositionStorage.single(capture_composition_object(host,target,expected_zone='battlefield'))
    else:
        root.permanent_composition.validate_carrier(root);prior=root.permanent_composition
    return PreparedCompositionMerge(root.to_dict(),prior,incoming,position,host.state.timestamp_sequence+1)
