from __future__ import annotations

"""Current or departure information for an already announced target incarnation."""

from collections.abc import Mapping
from dataclasses import dataclass

from .received_damage_model import RECEIVED_DAMAGE_EVENTS
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
        pin_occurrence_controller_departures(host,cards)
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


OCCURRENCE_CONTROLLER_INFORMATION_KIND='occurrence_controller_information'
OCCURRENCE_CONTROLLER_ENTRY_EVENTS = frozenset({
    'artifact.enter', 'creature.enter', 'enchantment.enter', 'land.enter', 'permanent.enter',
})
OCCURRENCE_CONTROLLER_GRAVEYARD_EVENTS = frozenset({
    'artifact.graveyard', 'creature.dies', 'permanent.graveyard',
})
_OCCURRENCE_LKI_FIELD='occurrence_controller_information_lki'


def occurrence_controller_reference():
    return {'kind': OCCURRENCE_CONTROLLER_INFORMATION_KIND, 'schema_version': 1}


def is_occurrence_controller_reference(value):
    return (isinstance(value, Mapping) and value == occurrence_controller_reference()
            and type(value.get('schema_version')) is int)


def _occurrence_reference(value):
    if not is_occurrence_controller_reference(value):
        raise ValueError('Occurrence controller information uses one closed schema')


def _uses_occurrence_controller(value):
    if isinstance(value,Mapping):
        if value.get('kind')==OCCURRENCE_CONTROLLER_INFORMATION_KIND:
            _occurrence_reference(value);return True
        return any(_uses_occurrence_controller(v) for v in value.values())
    if isinstance(value,(tuple,list)):return any(_uses_occurrence_controller(v) for v in value)
    return False


def _blocking_controller_identity(context):
    """Read the blocker that participated in the sealed declaration pair."""
    from .block_transitions import BlockTransitionEvent

    transition = BlockTransitionEvent.from_dict(context.get('block_transition'))
    participants = {participant.reference: participant for participant in transition.participants}
    blocker = participants.get(context.get('card'))
    attacker = participants.get(context.get('blocked_attacker'))
    if blocker is None or attacker is None or not any(
        row.blocker_object_id == blocker.object_id and row.attacker_object_id == attacker.object_id
        for row in transition.assignments
    ):
        raise ValueError('Blocking controller information requires the original blocking pair')
    return blocker.object_id, blocker.logical_object_id


def occurrence_controller_context(host, program, event, context):
    """Seal the original permanent for a public occurrence controller read."""
    if (event not in OCCURRENCE_CONTROLLER_ENTRY_EVENTS | RECEIVED_DAMAGE_EVENTS | {'creature.blocks'}
            or not _uses_occurrence_controller(program.effects)):
        return {}
    if event == 'creature.blocks':
        object_id, identity = _blocking_controller_identity(context)
    elif event in RECEIVED_DAMAGE_EVENTS:
        if (context.get('target_kind') != 'permanent' or 'creature' not in context.get('target_types', ())
                or type(context.get('amount')) is not int or context['amount'] <= 0):
            raise ValueError('Damage controller information requires a committed creature receipt')
        identity = context.get('target_logical_object_id')
        object_id = context.get('target_object_id')
    else:
        identity = context.get('card_object_identity')
        object_id = identity.rpartition('@')[0] if type(identity) is str else ''
    card = host.state.cards.get(object_id) if object_id else None
    if (card is None or card.zone != 'battlefield' or card.ref != context.get('card')
            or card.logical_object_id != identity or event == 'creature.blocks' and card.phased_out):
        raise ValueError('Occurrence controller information requires the original public permanent')
    return {'card_object_id': object_id, 'card_object_identity': identity}


def _occurrence_identity(item):
    object_id=item.context.get('card_object_id');identity=item.context.get('card_object_identity')
    if type(object_id) is not str or not object_id or type(identity) is not str or not identity:
        raise ValueError('Occurrence controller information requires a sealed public incarnation')
    return object_id,identity


def resolve_occurrence_controller_information(host,value,item):
    _occurrence_reference(value);object_id,identity=_occurrence_identity(item)
    card=host.state.cards.get(object_id)
    if card is not None and card.zone=='battlefield' and card.logical_object_id==identity:
        return host._effective_card_data(card).get('controller',card.controller)
    raw=item.context.get(_OCCURRENCE_LKI_FIELD)
    if not isinstance(raw,Mapping) or set(raw)!={'logical_object_id','information'} or raw['logical_object_id']!=identity:
        raise ValueError('Occurrence current or departure controller information is unavailable')
    info=TargetControllerInformation.from_dict(raw['information'])
    if info.object_id!=object_id or f'{info.object_id}@{info.zone_change_counter}'!=identity:
        raise ValueError('Occurrence controller departure identity changed')
    return info.controller


def pin_occurrence_controller_departures(host,cards):
    departing={c.object_id:c for c in cards};updates=[]
    for item in host.state.stack:
        program=host.semantics.get(item.semantic_key) if item.semantic_key else None
        if program is None or not _uses_occurrence_controller(program.effects):continue
        object_id,identity=_occurrence_identity(item);card=departing.get(object_id)
        if card is None or card.zone!='battlefield' or card.logical_object_id!=identity:continue
        controller=host._effective_card_data(card).get('controller',card.controller)
        updates.append((item,{'logical_object_id':identity,
            'information':TargetControllerInformation(card.object_id,card.zone_change_counter,controller).to_dict()}))
    for item,value in updates:item.context[_OCCURRENCE_LKI_FIELD]=value
    from .trigger_batches import PendingTriggerBatch
    from .trigger_processing import replace_pending_trigger_batch
    pending=[]
    for index,batch in enumerate(getattr(host.state,'pending_trigger_batches',())):
        data=batch.to_dict();changed=False
        for group in data['groups']:
            for frame in group['items']:
                program=host.semantics.get(frame.get('semantic_key'))
                if program is None or not _uses_occurrence_controller(program.effects):continue
                context=frame['context'];object_id=context.get('card_object_id');identity=context.get('card_object_identity')
                card=departing.get(object_id)
                if card is None or card.zone!='battlefield' or card.logical_object_id!=identity:continue
                controller=host._effective_card_data(card).get('controller',card.controller)
                context[_OCCURRENCE_LKI_FIELD]={'logical_object_id':identity,
                    'information':TargetControllerInformation(card.object_id,card.zone_change_counter,controller).to_dict()}
                changed=True
        if changed:pending.append((index,PendingTriggerBatch.from_dict(data)))
    for index,batch in pending:replace_pending_trigger_batch(host,index,batch)
