"""Normalize committed recipient totals without changing dealer packets."""
from .received_damage_model import (
    ReceivedDamageOccurrence, RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT,
)


def received_damage_occurrences(events, *, event_id: str):
    groups = {}
    for event in events:
        if type(event.dealt_amount) is not int or event.dealt_amount < 0:
            raise ValueError('Only exact committed damage may create receiving occurrences')
        if not event.dealt_amount:
            continue
        if type(event.combat) is not bool:
            raise ValueError('Received combat facts must be boolean')
        key = (event.target_kind, event.target, event.target_object_id, event.target_logical_object_id)
        facts = (event.target_controller, tuple(sorted(set(event.target_types))), tuple(sorted(set(event.target_subtypes))))
        if key not in groups:
            groups[key] = [facts, 0, 0]
        row = groups[key]
        if row[0] != facts:
            raise ValueError('A recipient incarnation has inconsistent simultaneous facts')
        row[1] += event.dealt_amount
        row[2] += event.dealt_amount if event.combat else 0
    return tuple(
        ReceivedDamageOccurrence(
            event_id=event_id, target=key[1], target_kind=key[0],
            target_object_id=key[2], target_logical_object_id=key[3],
            target_controller=groups[key][0][0], target_types=groups[key][0][1],
            target_subtypes=groups[key][0][2], amount=groups[key][1], combat_amount=groups[key][2],
        )
        for key in sorted(groups, key=lambda key: tuple(value or '' for value in key))
    )


def dispatch_received_damage_events(host, result, *, sources, source_zones, trigger_batch):
    for occurrence in received_damage_occurrences(result.events, event_id=result.event_batch_id):
        host._dispatch_semantic_event(
            RECEIVED_DAMAGE_EVENT, occurrence.semantic_context(), sources=sources,
            source_zones=source_zones, trigger_batch=trigger_batch,
        )
        if host._semantic_pause_annotation() is not None:
            return
        if occurrence.combat_amount:
            host._dispatch_semantic_event(
                RECEIVED_COMBAT_DAMAGE_EVENT, occurrence.semantic_context(combat_only=True),
                sources=sources, source_zones=source_zones, trigger_batch=trigger_batch,
            )
            if host._semantic_pause_annotation() is not None:
                return
