from __future__ import annotations

"""Expand coupled destruction results without mutating the battlefield."""

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from .destruction_replacement_options import DestructionReplacementSubject, destruction_replacement_options
from .replacement.immutable import FrozenMap, thaw_value
from .replacement.model import ReplacementEffect, ReplacementEventBatch
from .replacement.ordering import ReplacementChoiceRequired, advance_replacement_batch
from .umbra_armor_model import UmbraArmorProtection


@dataclass(frozen=True, slots=True)
class ResolvedDestructionReplacements:
    batch: ReplacementEventBatch
    effects: tuple[ReplacementEffect, ...]
    requested_object_ids: tuple[str, ...]
    damage_clear_object_ids: tuple[str, ...]
    dispositions: tuple[tuple[str, str], ...]
    consumed_selections: int


def resolve_destruction_replacements(
    subjects: Sequence[DestructionReplacementSubject],
    protections: Sequence[UmbraArmorProtection],
    requested_object_ids: Sequence[str],
    *, batch_id: str, apnap_order: Sequence[str], cause: str,
    regeneration_prohibited: bool = False,
    selections: Sequence[str | Mapping[str, object]] = (),
) -> ResolvedDestructionReplacements:
    """Replay decisions and expand mandatory Aura destruction before commit."""
    by_id = {subject.object_id: subject for subject in subjects}
    if len(by_id) != len(subjects):
        raise ValueError("Destruction replacement snapshot has duplicate subjects")
    requested = tuple(requested_object_ids)
    if not requested or len(requested) != len(set(requested)) or not set(requested).issubset(by_id):
        raise ValueError("Destruction replacement requests must name distinct snapshot subjects")
    current_ids = list(requested)
    expanded = set()
    redirected = {}
    damage_clear = set()
    supplied = tuple(selections)
    consumed = 0
    options = destruction_replacement_options(
        tuple(by_id[object_id] for object_id in current_ids), protections,
        batch_id=batch_id, apnap_order=apnap_order, cause=cause,
        regeneration_prohibited=regeneration_prohibited,
    )
    current = options.batch
    effects = options.effects
    for _ in range(len(by_id) + 1):
        progress = advance_replacement_batch(
            current, effects, selections=supplied[consumed:], require_all_selections=False,
        )
        consumed += progress.consumed_selections
        current = progress.batch
        if progress.pending is not None:
            raise ReplacementChoiceRequired(batch=current, effects=effects, pending=progress.pending)
        additions = []
        for event in current.events:
            object_id = str(event.payload["object_id"])
            if event.payload["disposition"] != "umbra_armor" or object_id in expanded:
                continue
            expanded.add(object_id)
            damage_clear.add(object_id)
            aura_id = event.payload["umbra_aura_object_id"]
            aura = by_id.get(aura_id)
            if aura is None or aura.logical_object_id != event.payload["umbra_aura_logical_object_id"]:
                raise ValueError("Chosen Umbra Aura is absent or stale in the destruction snapshot")
            redirected[object_id] = aura_id
            cursor = aura_id
            visited = set()
            while cursor in redirected and cursor not in visited:
                visited.add(cursor)
                cursor = redirected[cursor]
            if cursor in visited:
                # CR614.5: the modified destruction returned to a subject whose
                # chosen Aura replacement has already affected this event.
                changed = []
                for candidate in current.events:
                    if candidate.payload["object_id"] == cursor:
                        payload = thaw_value(candidate.payload)
                        payload["disposition"] = "destroy"
                        candidate = replace(candidate, payload=FrozenMap(payload))
                    changed.append(candidate)
                current = replace(current, events=tuple(changed))
                continue
            if aura_id not in current_ids:
                additions.append(aura_id)
                current_ids.append(aura_id)
        if not additions:
            if consumed != len(supplied):
                raise ValueError("Destruction replacement selections contain unused decisions")
            return ResolvedDestructionReplacements(
                current, effects, requested, tuple(sorted(damage_clear)),
                tuple(sorted((str(event.payload["object_id"]), str(event.payload["disposition"])) for event in current.events)),
                consumed,
            )
        added = destruction_replacement_options(
            tuple(by_id[object_id] for object_id in additions), protections,
            batch_id=batch_id, apnap_order=apnap_order, cause=cause,
            # A prohibition on the original recipient does not name a new Aura.
            regeneration_prohibited=False,
        )
        current = replace(current, events=(*current.events, *added.batch.events))
        effects = (*effects, *added.effects)
    raise ValueError("Destruction replacement expansion did not reach a finite result")
