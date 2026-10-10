from __future__ import annotations

"""Bind current destruction snapshots to the shared replacement planner."""

from typing import Any, Mapping, Sequence

from .counter_state import CounterChange, plan_counter_changes
from .destruction_replacement_options import DestructionReplacementSubject
from .destruction_replacement_planning import resolve_destruction_replacements
from .keyword_abilities import normalized_effective_keywords
from .replacement.immutable import thaw_value
from .umbra_armor import current_umbra_armor_protections


def prepare_destruction_replacement_if_needed(host, requests, **kwargs):
    if not requests:
        return None
    ids = {request.object_id for request in requests}
    protections = current_umbra_armor_protections(host)
    has_umbra = any(protection.recipient_object_id in ids for protection in protections)
    has_competing = kwargs["cause"].value == "effect" and not kwargs["regeneration_prohibited"] and any(
        host.state.cards[object_id].counters.get("shield", 0)
        and host.state.cards[object_id].regeneration_shields
        and "indestructible" not in normalized_effective_keywords(host, host.state.cards[object_id])
        for object_id in ids if object_id in host.state.cards
    )
    if not has_umbra and not has_competing:
        return None
    try:
        return prepare_replaced_destructions(host, requests, **kwargs)
    except ValueError as exc:
        from .replacement.ordering import ReplacementChoiceRequired
        from .destruction import DestructionError
        if isinstance(exc, ReplacementChoiceRequired):
            raise
        raise DestructionError(str(exc)) from exc


def current_destruction_subject(host: Any, object_id: str) -> DestructionReplacementSubject:
    card = host.state.cards.get(object_id)
    if card is None:
        raise ValueError("Destruction permanent does not exist")
    if card.zone != "battlefield" or card.phased_out:
        raise ValueError("Only a phased-in battlefield permanent can be destroyed")
    return DestructionReplacementSubject(
        card.object_id, card.ref, card.logical_object_id, card.owner, card.controller,
        "indestructible" in normalized_effective_keywords(host, card),
        card.counters.get("shield", 0), card.regeneration_shields,
    )


def legacy_destruction_entry(host: Any, request: Any, *, cause: Any, regeneration_prohibited: bool):
    from .destruction import DestructionEntry, DestructionError, _destruction_disposition
    try:
        subject = current_destruction_subject(host, request.object_id)
        if subject.logical_object_id != request.logical_object_id:
            raise ValueError("Destruction permanent changed logical identity")
        disposition = _destruction_disposition(cause=cause, indestructible=subject.indestructible,
            shield_counters=subject.shield_counters, regeneration_shields=subject.regeneration_shields,
            regeneration_prohibited=regeneration_prohibited)
        return DestructionEntry(subject.object_id, subject.object_ref, subject.logical_object_id, subject.controller,
                                disposition, subject.indestructible, subject.shield_counters, subject.regeneration_shields)
    except ValueError as exc:
        raise DestructionError(str(exc)) from exc


def prepare_replaced_destructions(
    host: Any, requests: Sequence[Any], *, cause: Any, actor: str | None,
    reason: str, regeneration_prohibited: bool,
    event_order: Sequence[str], replacement_selections: Sequence[str | Mapping[str, Any]],
):
    from .destruction import DestructionDisposition, DestructionEntry, DestructionPlan
    protections = current_umbra_armor_protections(host)
    requested = tuple(event_order) if event_order else tuple(request.object_id for request in requests)
    if len(requested) != len(set(requested)) or set(requested) != {request.object_id for request in requests}:
        raise ValueError("Destruction replacement event order must cover each request once")
    needed = set(requested)
    for _ in range(len(protections) + 1):
        expanded = needed | {protection.aura_object_id for protection in protections
                             if protection.recipient_object_id in needed}
        if expanded == needed:
            break
        needed = expanded
    relevant = tuple(protection for protection in protections if protection.recipient_object_id in needed)
    subjects = tuple(current_destruction_subject(host, object_id) for object_id in sorted(needed))
    by_id = {subject.object_id: subject for subject in subjects}
    for request in requests:
        if by_id[request.object_id].logical_object_id != request.logical_object_id:
            raise ValueError("Destruction requested incarnation changed")
    # Reconstruction is content-bound, rather than consuming a new engine ID.
    batch_id = "destruction:" + ":".join(by_id[object_id].logical_object_id for object_id in sorted(requested))
    resolved = resolve_destruction_replacements(
        subjects, relevant, requested, batch_id=batch_id, apnap_order=host.apnap_order(),
        cause=cause.value, regeneration_prohibited=regeneration_prohibited,
        selections=tuple(thaw_value(value) for value in replacement_selections),
    )
    entries = []
    shield_changes = []
    for object_id, disposition in resolved.dispositions:
        subject = by_id[object_id]
        entries.append(DestructionEntry(
            object_id, subject.object_ref, subject.logical_object_id, subject.controller,
            DestructionDisposition(disposition), subject.indestructible,
            subject.shield_counters, subject.regeneration_shields,
        ))
        if disposition == "shield_counter":
            shield_changes.append(CounterChange("permanent", object_id, "shield", -1,
                                                "battlefield", subject.logical_object_id))
    destroyed = tuple(entry.object_id for entry in entries if entry.disposition is DestructionDisposition.DESTROY)
    ordered = tuple(value for value in event_order if value in destroyed)
    ordered += tuple(value for value in destroyed if value not in ordered)
    return DestructionPlan(
        cause, actor, reason, tuple(entries), plan_counter_changes(host, shield_changes),
        regeneration_prohibited, ordered, (),
        requested_object_ids=requested, replacement_subjects=subjects,
        umbra_protections=relevant, destruction_replacement_selections=tuple(replacement_selections),
        damage_clear_object_ids=resolved.damage_clear_object_ids,
    )
