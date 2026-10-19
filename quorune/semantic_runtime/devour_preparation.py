from __future__ import annotations

"""Append captured Devour sacrifices to the existing immutable event boundary."""

from dataclasses import replace
from ..devour import DevourProposal,DevourQuality
from ..replacement import ReplacementEventBatch,ReplacementChoiceRequired,advance_replacement_batch,EntryReplacementScope
from ..replacement.entry_reservations import share_entry_reservations
from .zone_replacement_inputs import zone_change_snapshot_event


def _selected_subjects(snapshot,batch,error_type):
    captured={subject.object_id:subject for subject in snapshot.devour_candidates};selected={};per_entry={}
    unavailable={subject.object_id for subject in snapshot.subjects}
    for event in batch.events:
        ids=[]
        for value in event.payload.get('devour_proposals',()):
            proposal=DevourProposal.from_dict(value)
            for obj in proposal.objects:
                subject=captured.get(obj.object_id)
                if (subject is None or obj.object_id in unavailable or obj.object_id in selected
                        or subject.logical_object_id!=obj.logical_object_id or subject.object_ref!=obj.ref
                        or subject.controller!=proposal.controller):raise error_type('Devour selected an uncaptured, entering or repeated incarnation')
                types=set(subject.object_types)
                required={'artifact','food'} if proposal.spec.quality is DevourQuality.FOOD else {proposal.spec.quality.value}
                if not required<=types:raise error_type('Devour quality no longer matches its captured sacrifice subject')
                selected[obj.object_id]=subject;ids.append(obj.object_id)
        per_entry[event.affected_object.object_id]=tuple(ids)
    return selected,per_entry


def prepare_devour_snapshot(snapshot,*,selections,error_type):
    from .zone_replacements import _prepared_from_event
    values=tuple(selections)
    scope=EntryReplacementScope(tuple(subject.object_id for subject in snapshot.subjects if subject.destination=='battlefield'),
        tuple(subject.object_id for subject in snapshot.subjects if subject.destination=='battlefield' and subject.origin=='library'))
    leaving=tuple(subject.object_id for subject in snapshot.subjects if subject.destination!='battlefield')
    if leaving:scope=scope.reserve_zone_changes(leaving)
    events=tuple(replace(zone_change_snapshot_event(snapshot,subject),entry_scope=scope) for subject in snapshot.subjects)
    batch=ReplacementEventBatch(f'replacement:zone.batch:{snapshot.revision}:{snapshot.event_sequence+1}',events,snapshot.apnap_order)
    progress=advance_replacement_batch(batch,snapshot.effects,selections=values,require_all_selections=False)
    if progress.pending is not None:raise ReplacementChoiceRequired(batch=progress.batch,effects=snapshot.effects,pending=progress.pending)
    selected,per_entry=_selected_subjects(snapshot,progress.batch,error_type)
    added=tuple(replace(zone_change_snapshot_event(snapshot,subject),entry_scope=scope) for subject in selected.values())
    combined=replace(progress.batch,events=share_entry_reservations((*progress.batch.events,*added)))
    final=advance_replacement_batch(combined,snapshot.effects,selections=values[progress.consumed_selections:])
    if final.pending is not None:raise ReplacementChoiceRequired(batch=final.batch,effects=snapshot.effects,pending=final.pending)
    events={event.affected_object.object_id:event for event in final.batch.events};prepared={}
    for subject in selected.values():
        event=events[subject.object_id]
        prepared[subject.object_id]=_prepared_from_event(subject,event,state_revision=snapshot.revision,event_sequence=snapshot.event_sequence,
            effects=snapshot.effects,journal=tuple(s for s in final.batch.journal if s.event_id==event.event_id))
    for root in snapshot.subjects:
        sacrifices=tuple(prepared[obj] for obj in per_entry[root.object_id])
        if any(v.destination=='battlefield' for v in sacrifices):raise error_type('A prevented sacrifice requires its separate represented Devour result contract')
        event=events[root.object_id]
        prepared[root.object_id]=_prepared_from_event(root,event,state_revision=snapshot.revision,event_sequence=snapshot.event_sequence,
            effects=snapshot.effects,journal=tuple(s for s in final.batch.journal if s.event_id==event.event_id),devour_sacrifices=sacrifices)
    return {subject.object_id:prepared[subject.object_id] for subject in snapshot.subjects}
