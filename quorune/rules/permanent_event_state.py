"""Capture public state through the existing authoritative object query."""
from ..permanent_event_state_model import (PermanentEventState,PERMANENT_EVENT_STATE_KEY,
    permanent_event_state_version)


def captured_permanent_event_state(host,card,characteristics=None):
    if (permanent_event_state_version(getattr(host.state,'permanent_event_state_version',None)) is None
            or card.zone!='battlefield' and not characteristics):return None
    if characteristics is not None and PERMANENT_EVENT_STATE_KEY in characteristics:
        return PermanentEventState.from_dict(characteristics[PERMANENT_EVENT_STATE_KEY])
    if card.zone!='battlefield':return None
    row=host._public_object_query_result(card)
    return PermanentEventState(row.tapped,row.attacking,row.blocking,row.enchanted,row.equipped,
        row.modified,row.monstrous_value is not None)


def permanent_event_characteristics(host,card,characteristics):
    facts=captured_permanent_event_state(host,card,characteristics)
    if facts is None:return characteristics
    return {**characteristics,PERMANENT_EVENT_STATE_KEY:facts.to_dict()}


def current_zone_event_characteristics(host,card):
    return permanent_event_characteristics(host,card,host._effective_card_data(card))
