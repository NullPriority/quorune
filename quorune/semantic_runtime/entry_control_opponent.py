from __future__ import annotations

"""Refresh prospective reads after choosing an entering permanent's controller."""

from dataclasses import dataclass, replace

from ..entry_designations import ENTRY_CONTROL_OPPONENT_HANDLER_ID, ENTRY_OPPONENT_FIELD, EntryOpponentChoice
from ..replacement_effects import (
    ReplacementChoiceRequired, ReplacementClass, ReplacementEventBatch,
    advance_replacement_batch, replacement_choice,
)
from .zone_replacement_inputs import zone_change_snapshot_events
from .zone_replacement_model import ZoneChangeReplacementSnapshot


def _is_controller_effect(effect):
    return (
        effect.replacement_class is ReplacementClass.ENTERS_CONTROL
        and effect.effect_id.startswith(f"{ENTRY_CONTROL_OPPONENT_HANDLER_ID}:")
    )


@dataclass(frozen=True, slots=True)
class PreparedEntryControllers:
    original: ZoneChangeReplacementSnapshot
    snapshot: ZoneChangeReplacementSnapshot
    batch: ReplacementEventBatch
    consumed_selections: int

    def retain_control_journal(self, prepared):
        by_id = {event.affected_object.object_id: event for event in self.batch.events}
        return {
            subject.object_id: self._retain_control(prepared[subject.object_id], subject, by_id.get(subject.object_id))
            for subject in self.original.subjects
        }

    def _retain_control(self, result, subject, original_event):
        if original_event is None:
            return result
        journal = tuple(row for row in self.batch.journal if row.event_id == original_event.event_id)
        selected = {row.effect_id for row in journal}
        return replace(
            result, destination_controller=subject.destination_controller,
            journal=(*journal, *result.journal),
            effects=(*result.effects, *(effect for effect in self.original.effects if effect.effect_id in selected)),
        )


def prepare_entry_controller_choices(snapshot, *, capture, destination_controllers, selections):
    effects = tuple(effect for effect in snapshot.effects if _is_controller_effect(effect))
    if not effects:
        return None
    events = tuple(event for event in zone_change_snapshot_events(snapshot) if replacement_choice(event, effects))
    if not events:
        return None
    progress = advance_replacement_batch(
        ReplacementEventBatch(
            batch_id=f"replacement:zone.batch:{snapshot.revision}:{snapshot.event_sequence + 1}",
            events=events,
            apnap_order=snapshot.apnap_order,
        ),
        effects,
        selections=selections,
        require_all_selections=False,
    )
    if progress.pending is not None:
        raise ReplacementChoiceRequired(batch=progress.batch, effects=effects, pending=progress.pending)
    changed = {
        event.affected_object.object_id: EntryOpponentChoice.from_dict(event.payload[ENTRY_OPPONENT_FIELD]).seat
        for event in progress.batch.events
    }
    refreshed = capture({**(destination_controllers or {}), **changed})
    refreshed = replace(refreshed, effects=tuple(effect for effect in refreshed.effects if not _is_controller_effect(effect)))
    return PreparedEntryControllers(snapshot, refreshed, progress.batch, progress.consumed_selections)


def prepare_controlled_entry_batch(host, *, capture, prepare, destination_controllers, selections, error_type):
    from ..effect_runtime.hand_inspection import validate_entry_hand_reveal
    from ..entry_reveal import ENTRY_REVEAL_FIELD
    from ..rules.amplify import amplify_hand_reveals

    snapshot = capture(destination_controllers)
    entry_controllers = prepare_entry_controller_choices(
        snapshot, capture=capture, destination_controllers=destination_controllers, selections=selections,
    )
    if entry_controllers is not None:
        snapshot = entry_controllers.snapshot
        selections = selections[entry_controllers.consumed_selections:]
    prepared = prepare(snapshot, selections=selections, error_type=error_type, host=host)
    if entry_controllers is not None:
        prepared = entry_controllers.retain_control_journal(prepared)
    for result in prepared.values():
        payload = result.event.payload
        for selection in (payload.get(ENTRY_REVEAL_FIELD), *amplify_hand_reveals(payload)):
            validate_entry_hand_reveal(host, selection)
    return prepared
