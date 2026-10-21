from __future__ import annotations

"""CR616 choices across one entry and its current counter instruction."""

from dataclasses import replace
from typing import Iterable

from .applicability import canonical_effects, replacement_choice
from .model import ReplaceableEvent, ReplacementClass, ReplacementEffect
from .operations import AddAffectedObjectCounter, ApplyEntryCounterReplacement


def entry_counter_order_effects(
    event: ReplaceableEvent, effects: Iterable[ReplacementEffect]
) -> tuple[ReplacementEffect, ...]:
    values=tuple(effects)
    if event.kind != 'zone.change' or not event.children or event.payload.get('destination') != 'battlefield':
        return values
    values=canonical_effects(values)
    # Retain historical trees unless the new additional-entry owner participates.
    if not any(isinstance(op, AddAffectedObjectCounter) for effect in values for op in effect.operations):
        return values
    root_choice = replacement_choice(event, values)
    if root_choice is not None and root_choice.replacement_class < ReplacementClass.OTHER:
        return values
    adapted = {effect.effect_id: effect for effect in values}
    candidates=tuple(child for child in event.children if child.kind=='counter.place'
        and child.affected_object is not None and event.affected_object is not None
        and child.affected_object.object_id==event.affected_object.object_id
        and child.payload.get('follows_zone_destination') is True
        and child.payload.get('counter_name')=='+1/+1')
    if len(candidates)!=1:return values
    for child in candidates:
        choice = replacement_choice(child, values)
        if choice is None:
            continue
        for identity in choice.options:
            effect = adapted[identity]
            if effect.event_kind!='counter.place':
                # Separate counter kinds keep their ordinary child boundaries.
                continue
            adapted[identity] = replace(effect, event_kind='zone.change', conditions={},
                operations=(ApplyEntryCounterReplacement(child.event_id, effect),), decline_operations=())
    return canonical_effects(adapted.values())
