from __future__ import annotations

"""Versioned CR 701.50 instruction and retained conniving object identity."""

from dataclasses import dataclass
from typing import Any, Mapping

CONNIVE_OPERATION = 'connive'
CONNIVE_CAPABILITY = 'hand.connive.fixed_count'
CONNIVE_MECHANIC = 'fixed-connive'


@dataclass(frozen=True, slots=True)
class FixedConniveSpec:
    count: int = 1
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported Connive schema version')
        if type(self.count) is not int or not 0 <= self.count <= 20:
            raise ValueError('Fixed Connive requires an integral count from zero through twenty')

    def to_dict(self):
        return {'schema_version':self.schema_version,'count':self.count}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','count'}:
            raise ValueError('Connive spec has a closed schema')
        return cls(value['count'],value['schema_version'])


def connive_effect(spec: FixedConniveSpec, *, card: str, player: str):
    if type(card) is not str or not card or type(player) is not str or not player:
        raise ValueError('Connive requires explicit object and controller bindings')
    return {'op':CONNIVE_OPERATION,'card':card,'player':player,'spec':spec.to_dict()}


@dataclass(frozen=True, slots=True)
class ConnivingObject:
    object_id: str
    logical_object_id: str
    ref: str
    controller: str

    def __post_init__(self):
        if any(type(v) is not str or not v for v in (self.object_id,self.logical_object_id,self.ref,self.controller)):
            raise ValueError('Conniving object requires complete retained identity and controller')

    def to_dict(self):
        return {'object_id':self.object_id,'logical_object_id':self.logical_object_id,'ref':self.ref,'controller':self.controller}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'object_id','logical_object_id','ref','controller'}:
            raise ValueError('Conniving object identity has a closed schema')
        return cls(**dict(value))


def source_connive_instructions(effects):
    for value in effects:
        if not isinstance(value, Mapping):
            continue
        if value.get('op') == CONNIVE_OPERATION and value.get('card') == '$source':
            return True
        for key in ('effects', 'then_effects', 'else_effects'):
            nested = value.get(key)
            if isinstance(nested, (list, tuple)) and source_connive_instructions(nested):
                return True
    return False


def connive_trigger_departure_context(host, source, program):
    if CONNIVE_CAPABILITY not in program.capability_dependencies:
        return {}
    current = host.state.cards.get(source.object_id)
    if not source_connive_instructions(program.effects) or (current is not None
            and current.zone == 'battlefield' and current.logical_object_id == source.logical_object_id):
        return {}
    return {'connive_source_lki': ConnivingObject(source.object_id, source.logical_object_id,
                                                 source.ref, source.controller).to_dict()}


def capture_connive_source_departure(host, card):
    """Retain CR 701.50b's controller before the source leaves its incarnation."""
    items=list(host.state.stack)
    for batch in host.state.pending_trigger_batches:
        for group in batch.get('groups',()):items.extend(group.get('items',()))
    for item in items:
        get=lambda field: item.get(field) if isinstance(item,Mapping) else getattr(item,field)
        context=get('context')
        if get('source_object_id')!=card.object_id or context.get('source_logical_object_id')!=card.logical_object_id:
            continue
        program=host.semantics.get(get('semantic_key'))
        if program is not None and source_connive_instructions(program.effects):
            context.setdefault('connive_source_lki',ConnivingObject(card.object_id,card.logical_object_id,card.ref,card.controller).to_dict())


def connive_source_controller(item, cards):
    source=cards.get(item.source_object_id or item.card_object_id or '')
    expected=item.context.get('source_logical_object_id')
    if source is not None and source.zone=='battlefield' and source.logical_object_id==expected:
        return source.controller
    retained=item.context.get('connive_source_lki')
    if retained is None:
        raise ValueError('Connive requires retained last-known source controller after departure')
    retained = ConnivingObject.from_dict(retained)
    if retained.object_id != (item.source_object_id or item.card_object_id) or retained.logical_object_id != expected:
        raise ValueError('Connive last-known source identity differs from its stack instruction')
    return retained.controller


def bind_connive_instruction(host,item,effect):
    """Capture recipient identity before the draw phase can suspend."""
    if effect.get('op')!=CONNIVE_OPERATION or '_connive_stage' in effect:
        return effect
    if set(effect)!={'op','card','player','spec'}:
        raise ValueError('Connive instruction has unknown or missing fields')
    FixedConniveSpec.from_dict(effect['spec'])
    ref=effect['card']
    card=next((card for card in host.state.cards.values() if card.ref==ref),None)
    if card is None:
        raise ValueError('Conniving object is unavailable')
    expected=item.context.get('source_logical_object_id') if card.object_id==item.source_object_id else None
    if expected and (card.zone!='battlefield' or card.logical_object_id!=expected):
        retained=ConnivingObject.from_dict(item.context.get('connive_source_lki'))
        if retained.object_id != card.object_id or retained.logical_object_id != expected or retained.ref != ref:
            raise ValueError('Connive retained source identity changed')
    else:
        if card.zone!='battlefield' or card.phased_out:
            raise ValueError('Connive recipient must be a current phased-in permanent')
        retained=ConnivingObject(card.object_id,card.logical_object_id,card.ref,card.controller)
    if effect['player']!=retained.controller:
        raise ValueError('Connive controller binding differs from current or retained controller')
    return {**dict(effect),'_connive_stage':'draw','_conniver':retained.to_dict()}


def connive_discard_characteristic_ids(host, actor, effect):
    """CR 701.9c leaves unrevealed hidden redirected discards undefined."""
    selected = effect.get('_discards')
    if not isinstance(selected, Mapping):
        return ()
    result = []
    current_by_previous = {f'{card.object_id}@{card.zone_change_counter - 1}': card
                           for card in host.state.cards.values()}
    for event in host._current_turn_history('card_discarded'):
        if event.actor != actor or not event.object_incarnation:
            continue
        identity = event.object_incarnation
        if identity not in selected:
            continue
        card = current_by_previous.get(identity)
        if card is None:
            continue
        if card.zone in {'graveyard', 'exile', 'battlefield', 'stack', 'command'} and not card.face_down:
            result.append(identity)
        elif set(host.active_seats) <= set(card.revealed_to):
            result.append(identity)
    return tuple(result)
