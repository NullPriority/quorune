"""Card-component views inside the existing replacement event and chooser."""
from dataclasses import replace

from ..composition_zone_replacement_model import (COMPONENTS_FIELD,RESULT_FIELD,
    CardZoneComponent,card_zone_components)
from .immutable import thaw_value
from .model import ReplaceableEvent


def event_card_components(event):
    if COMPONENTS_FIELD not in event.payload:
        if RESULT_FIELD in event.payload:raise ValueError('A component result requires its captured card scope')
        return ()
    if (event.kind!='zone.change' or event.payload.get('origin')!='battlefield'
            or event.payload.get('object_kind')!='noncard' or event.affected_object is None):
        raise ValueError('Card-component replacement scope requires a departing token permanent')
    raw=event.payload[COMPONENTS_FIELD]
    if not isinstance(raw,(list,tuple)):raise ValueError('Card-component identities must be an array')
    return card_zone_components(tuple(CardZoneComponent.from_dict(row) for row in raw))


def card_component_event(event):
    """Return the card group without creating independent replacement choices."""
    if not event_card_components(event):return None
    payload=thaw_value(event.payload)
    payload.pop(COMPONENTS_FIELD)
    raw=payload.pop(RESULT_FIELD,None)
    if raw is None:
        return replace(event,event_id=event.event_id+':cards',payload={**payload,'object_kind':'card'})
    result=ReplaceableEvent.from_dict(thaw_value(raw))
    if (result.event_id!=event.event_id+':cards' or result.kind!='zone.change'
            or result.affected_object!=event.affected_object or result.affected_player!=event.affected_player
            or result.entry_scope is not None or result.payload.get('object_kind')!='card'
            or result.payload.get('origin')!='battlefield'
            or result.payload.get('object_ref')!=event.payload.get('object_ref')
            or result.payload.get('logical_object_id')!=event.payload.get('logical_object_id')
            or any(result.payload.get(field)!=event.payload.get(field) for field in ('owner','object_types','transition_kind'))
            or result.payload.get('destination') not in {'graveyard','exile','hand','library','command','outside'}
            or COMPONENTS_FIELD in result.payload or RESULT_FIELD in result.payload
            or not set(result.applied_effects).issubset(event.applied_effects)):
        raise ValueError('Card-component result does not belong to this departure')
    return result


def card_only_replacement(effect):
    condition=effect.conditions.get('object_kind')
    return effect.event_kind=='zone.change' and (condition=='card' or condition=={'eq':'card'})


def replacement_condition_event(event,effect):
    if COMPONENTS_FIELD not in event.payload and RESULT_FIELD not in event.payload:return event
    group=card_component_event(event)
    return group if card_only_replacement(effect) else event
