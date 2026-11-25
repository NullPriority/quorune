from __future__ import annotations

"""Materialize ordered graveyard copy costs before completing the entry tree."""

from dataclasses import replace

from ..replacement import ReplacementChoiceRequired, ReplacementEventBatch, advance_replacement_batch
from ..replacement.application import _create_affected_object_counter_event
from ..replacement.entry_reservations import share_entry_reservations
from ..replacement.operations import GraveyardCopyEntryOperation
from ..rules.linked_graveyard_entry import (
    LinkedGraveyardEntryOperation, LinkedGraveyardEntrySelection, linked_graveyard_entry_counters,
    materialize_linked_graveyard_entry, prepare_linked_graveyard_entry, unprepared_linked_graveyard_entry_choices,
)
from ..rules.graveyard_entry_copy import (
    GraveyardCopyPair, _after_exile_read_view, current_graveyard_entry_copy_counter,
    materialize_graveyard_copy_entry, prepare_graveyard_copy_inputs, unprepared_graveyard_copy_occurrences,
)
from .zone_preparation_results import prepared_from_zone_event
from .zone_replacement_inputs import snapshot_entry_scope, zone_change_snapshot_event


def has_graveyard_copy_operation(snapshot):
    return any(isinstance(op, (GraveyardCopyEntryOperation, LinkedGraveyardEntryOperation))
               for effect in snapshot.effects for op in effect.operations)


def _inherited_copy_replacements(reader, subject, source, target, event, *, prospective):
    from .entry_copy import _copied_entry_effects, _copy_form_view, entry_copy_replacement_effects
    from ..replacement.tribute import copied_tribute_replacement

    values = event.payload["entry_copy"]
    key = event.payload["entry_copy_form"]
    view = _copy_form_view(source,values)
    copied = _copied_entry_effects(reader,subject,source,target,values,form_key=key,simultaneous_departures=())
    enabled=tuple(copied_tribute_replacement(effect,prefix=key+":",form_key=key)
        or replace(effect,effect_id=effect.effect_id+":"+key) for effect in copied)
    return (*enabled,*entry_copy_replacement_effects(reader,subject,
         prospective_objects={**prospective,subject.object_id:view},initial_form=key))


def _append_graveyard_counter(event):
    from ..replacement.devour import combine_devour_entry_counters

    copied = current_graveyard_entry_copy_counter(event)
    counters = (*((copied,) if copied is not None else ()), *linked_graveyard_entry_counters(event))
    if not counters:
        return event
    children = list(event.children)
    for counter in counters:
        _create_affected_object_counter_event(event, event.payload, children, counter, effect_id=counter.source_ref)
    # Same event and same counter type are one placement, even when printed
    # creature-entry counters supplied another contribution to this result.
    combine_devour_entry_counters(event,event.payload,children,force=True)
    return replace(event,children=tuple(children))


def prepare_graveyard_copy_snapshot(host, snapshot, *, selections, error_type, prospective_objects=None):
    """Resolve copied entry replacements only after their ordered exile plans."""
    from .counter_permissions import copied_entry_counter_permissions
    from .devour_preparation import _selected_subjects
    from .entry_copy import _copy_form_view

    prospective=dict(prospective_objects or {})
    scope=snapshot_entry_scope(snapshot)
    events=tuple(replace(zone_change_snapshot_event(snapshot,subject),entry_scope=scope) for subject in snapshot.subjects)
    batch=ReplacementEventBatch(f"replacement:zone.batch:{snapshot.revision}:{snapshot.event_sequence+1}",events,snapshot.apnap_order)
    effects={effect.effect_id:effect for effect in snapshot.effects}
    subjects={subject.object_id:subject for subject in snapshot.subjects}
    linked={subject.object_id:[] for subject in snapshot.subjects}
    reader=host
    offset=0
    available=tuple(selections)
    while True:
        zone_effects=tuple(effect for effect in effects.values() if effect.event_kind!="counter.place")
        progress=advance_replacement_batch(batch,zone_effects,selections=available[offset:],
                                           require_all_selections=False,stop_for_graveyard_copy=True)
        if progress.pending is not None:
            raise ReplacementChoiceRequired(batch=progress.batch,effects=zone_effects,pending=progress.pending)
        offset+=progress.consumed_selections
        batch=progress.batch
        pending=next(((index,event,row) for index,event in enumerate(batch.events)
                      for row in (*unprepared_graveyard_copy_occurrences(event),
                                  *unprepared_linked_graveyard_entry_choices(event))), None)
        if pending is None:break
        index,event,row=pending
        subject=subjects[event.affected_object.object_id]
        if isinstance(row, LinkedGraveyardEntrySelection):
            try:
                prepared = prepare_linked_graveyard_entry(reader, row, replacement_selections=available[offset:])
                resolved = materialize_linked_graveyard_entry(event, prepared)
            except ValueError as exc:
                raise error_type(str(exc)) from exc
            offset += prepared.consumed_selections
            linked[subject.object_id].extend(prepared.exiles)
            reader = _after_exile_read_view(reader, prepared.exiles)
            batch = replace(batch, events=(*batch.events[:index], resolved, *batch.events[index + 1:]))
            continue
        pair=GraveyardCopyPair.from_dict(row["pair"])
        try:
            prepared=prepare_graveyard_copy_inputs(reader,pair,occurrence=row["component_id"],
                replacement_selections=available[offset:])
            resolved=materialize_graveyard_copy_entry(event,row["component_id"],prepared)
        except ValueError as exc:
            raise error_type(str(exc)) from exc
        offset+=prepared.consumed_selections
        linked[subject.object_id].extend(prepared.exiles)
        reader=_after_exile_read_view(reader,prepared.exiles)
        source=prospective.get(subject.object_id) or host.state.cards.get(subject.object_id)
        if source is None:raise error_type("Graveyard copy lost its prospective entering object")
        target=reader.state.cards[pair.copy_card.object_id]
        if target.zone!="exile" or target.face_down:
            target=host.state.cards[pair.copy_card.object_id]
        permissions=copied_entry_counter_permissions(reader,subject,_copy_form_view(source,resolved.payload["entry_copy"]))
        if permissions.rules or subject.counter_permissions.rules:
            resolved=replace(resolved,payload={**resolved.payload,"_counter_permissions":permissions.to_dict()})
        enabled=_inherited_copy_replacements(reader,subject,source,target,resolved,prospective=prospective)
        effects.update({effect.effect_id:effect for effect in enabled})
        batch=replace(batch,events=(*batch.events[:index],resolved,*batch.events[index+1:]))
    devour, by_entry=_selected_subjects(snapshot,batch,error_type)
    sacrifices=tuple(replace(zone_change_snapshot_event(snapshot,subject),entry_scope=scope) for subject in devour.values())
    entered=tuple(_append_graveyard_counter(event) for event in batch.events)
    final_batch=replace(batch,events=share_entry_reservations((*entered,*sacrifices)))
    all_effects=tuple(effects.values())
    final=advance_replacement_batch(final_batch,all_effects,selections=available[offset:])
    if final.pending is not None:
        raise ReplacementChoiceRequired(batch=final.batch,effects=all_effects,pending=final.pending)
    by_object={event.affected_object.object_id:event for event in final.batch.events}

    def prepare(subject, **costs):
        event=by_object[subject.object_id]
        return prepared_from_zone_event(subject,event,state_revision=snapshot.revision,event_sequence=snapshot.event_sequence,
            effects=all_effects,journal=tuple(value for value in final.batch.journal if value.event_id==event.event_id),**costs)
    sacrifices={subject.object_id:prepare(subject) for subject in devour.values()}
    if any(plan.destination=="battlefield" for plan in sacrifices.values()):
        raise error_type("A prevented sacrifice requires its represented Devour result contract")
    return {subject.object_id:prepare(subject,devour_sacrifices=tuple(sacrifices[oid] for oid in by_entry[subject.object_id]),
         graveyard_copy_exiles=tuple(linked[subject.object_id])) for subject in snapshot.subjects}
