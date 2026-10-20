from __future__ import annotations

"""Current or departure information for an already announced target incarnation."""

from collections.abc import Mapping
from dataclasses import dataclass

from .selection.target_query import target_identity_matches_snapshot

TARGET_CONTROLLER_INFORMATION_KIND='target_controller_information'
TARGET_CONTROLLER_INFORMATION_CAPABILITY='resolution.target_controller_information'
TARGET_CONTROLLER_INFORMATION_MECHANIC='fixed-target-controller-information'
_LKI_FIELD='target_controller_information_lki'


def target_controller_reference(index=0):
    return {'kind':TARGET_CONTROLLER_INFORMATION_KIND,'schema_version':1,'index':index}


def controller_reference_index(value):
    if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','index'}
            or value['kind']!=TARGET_CONTROLLER_INFORMATION_KIND or type(value['schema_version']) is not int
            or value['schema_version']!=1 or type(value['index']) is not int or value['index']<0):
        raise ValueError('Target controller information requires one closed target index')
    return value['index']


@dataclass(frozen=True,slots=True)
class TargetControllerInformation:
    object_id:str
    zone_change_counter:int
    controller:str

    def __post_init__(self):
        if (type(self.object_id) is not str or not self.object_id or type(self.controller) is not str or not self.controller
                or type(self.zone_change_counter) is not int or self.zone_change_counter<0):
            raise ValueError('Target controller information requires an exact incarnation and controller')

    def to_dict(self):
        return {'schema_version':1,'object_id':self.object_id,'zone_change_counter':self.zone_change_counter,'controller':self.controller}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','object_id','zone_change_counter','controller'}
                or type(value['schema_version']) is not int or value['schema_version']!=1):
            raise ValueError('Target controller departure information uses a closed schema')
        return cls(value['object_id'],value['zone_change_counter'],value['controller'])


def _snapshot(item,index):
    if index>=len(item.targets) or item.targets[index] is None:return None
    value=item.context.get('target_snapshots',{}).get(item.targets[index])
    if (not isinstance(value,Mapping) or type(value.get('object_id')) is not str
            or type(value.get('zone_change_counter')) is not int):
        raise ValueError('Referenced target incarnation is unavailable')
    return value


def resolve_target_controller_information(host,value,item):
    index=controller_reference_index(value);snapshot=_snapshot(item,index)
    if snapshot is None:return None
    card=host.state.cards.get(snapshot['object_id'])
    if (card is not None and card.zone=='battlefield' and not card.phased_out
            and target_identity_matches_snapshot(host,item.targets[index],snapshot)):
        return host._effective_card_data(card).get('controller',card.controller)
    raw=item.context.get(_LKI_FIELD,{}).get(str(index))
    if raw is None:raise ValueError('Referenced target current or last-known controller is unavailable')
    info=TargetControllerInformation.from_dict(raw)
    if (info.object_id,info.zone_change_counter)!=(snapshot['object_id'],snapshot['zone_change_counter']):
        raise ValueError('Target controller departure information names another incarnation')
    return info.controller


def _reference_indices(value):
    if isinstance(value,Mapping):
        if value.get('kind')==TARGET_CONTROLLER_INFORMATION_KIND:return {controller_reference_index(value)}
        return set().union(*(_reference_indices(v) for v in value.values())) if value else set()
    if isinstance(value,(list,tuple)):return set().union(*(_reference_indices(v) for v in value)) if value else set()
    return set()


def pin_target_controller_departures(host,cards,*,error_type=ValueError):
    """Use the existing zone owner's atomic departure capture boundary."""
    try:
        departing={(c.object_id,c.zone_change_counter):c for c in cards};updates=[]
        for item in host.state.stack:
            program=host.semantics.get(item.semantic_key) if item.semantic_key else None
            indices=_reference_indices((program.effects if program else (),item.context.get('dynamic_effects',()),item.context.get('cast_option_effects',())))
            if not indices:continue
            old=item.context.get(_LKI_FIELD,{})
            if not isinstance(old,Mapping):raise ValueError('Target controller departure book is malformed')
            captured=dict(old)
            for index in indices:
                snapshot=_snapshot(item,index)
                if snapshot is None:continue
                card=departing.get((snapshot['object_id'],snapshot['zone_change_counter']))
                if card is None or card.zone!='battlefield':continue
                controller=host._effective_card_data(card).get('controller',card.controller)
                captured[str(index)]=TargetControllerInformation(card.object_id,card.zone_change_counter,controller).to_dict()
            if captured!=old:updates.append((item,captured))
        for item,captured in updates:item.context[_LKI_FIELD]=captured
    except (TypeError,ValueError,KeyError) as exc:raise error_type(str(exc)) from exc
