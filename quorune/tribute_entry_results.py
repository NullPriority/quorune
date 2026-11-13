from __future__ import annotations

"""Committed, per-incarnation Tribute facts for normalized entry events."""

from collections.abc import Mapping
from .tribute_model import TributeEntryOutcome,TRIBUTE_ENTRY_EVENT,TRIBUTE_ENTRY_OUTCOMES,TRIBUTE_UNPAID_FIELD
from .replacement.tribute import validate_tribute_choice_event

def tribute_decision_events(event):
    if event.kind==TRIBUTE_ENTRY_EVENT:yield event
    for child in event.children:yield from tribute_decision_events(child)


def commit_tribute_entry_outcomes(prepared,card):
    if card.zone!='battlefield':return
    outcomes={}
    for event in tribute_decision_events(prepared.event):
        spec,subject=validate_tribute_choice_event(event)
        if (type(event.payload['accepted']) is not bool or subject.object_id!=card.object_id
                or (event.payload['accepted'] and (len(event.children)!=1 or event.children[0].kind!='counter.place'))
                or (not event.payload['accepted'] and event.children) or spec.link_id in outcomes):
            raise ValueError('Completed Tribute outcome lost its unique entering subject or counter instruction')
        p=event.payload
        outcomes[spec.link_id]=TributeEntryOutcome(spec,p['entry_id'],card.logical_object_id,
            p['controller'],p['opponent'],p['accepted']).to_dict()
    if outcomes:card.annotations[TRIBUTE_ENTRY_OUTCOMES]=outcomes


def tribute_entry_context(card,occurrence):
    raw=card.annotations.get(TRIBUTE_ENTRY_OUTCOMES)
    if raw is None:return {}
    if not isinstance(raw,Mapping):raise ValueError('Tribute entry outcomes must be a keyed typed map')
    unpaid=[]
    for link,value in raw.items():
        outcome=TributeEntryOutcome.from_dict(value)
        if link!=outcome.spec.link_id:raise ValueError('Tribute outcome ability link changed')
        if outcome.is_unpaid_entry(link_id=link,logical_object_id=occurrence.current_logical_object_id):
            unpaid.append(link)
    return {TRIBUTE_UNPAID_FIELD:sorted(unpaid)}
