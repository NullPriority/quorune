from __future__ import annotations

"""Read-only aggregation of fixed counter-removal prevention results."""

from dataclasses import dataclass, replace
from copy import copy
from typing import Any, Sequence

from .counter_removal import (
    CounterRemoval, CounterRemovalEffectPlan, CounterRemovalResult,
    plan_counter_removal_effect, validate_counter_removal_effect_plan, commit_counter_removal_effect,
)
from .replacement_effects import ReplaceableEvent, ReplacementEffect


HANDLER = 'prevention.damage.fixed-self-counter.v1'


@dataclass(frozen=True, slots=True)
class PreparedCounterPrevention:
    effect_id: str
    source_ref: str
    event_ids: tuple[str, ...]
    plan: CounterRemovalEffectPlan


def projected_counter_prevention_host(host: Any, prepared: Sequence[PreparedCounterPrevention]) -> Any:
    """Read-only preparation copy; canonical owners alone commit live counters."""
    if not prepared:
        return host
    projected = copy(host)
    cards = dict(host.state.cards)
    for result in prepared:
        transition, = result.plan.counter_plan.transitions
        card = cards[transition.subject_id]
        counters = dict(card.counters)
        if transition.after:
            counters[transition.counter_name] = transition.after
        else:
            counters.pop(transition.counter_name, None)
        cards[card.object_id] = replace(card, counters=counters)
    projected.state = replace(host.state, cards=cards)
    return projected


def prepare_counter_prevention_results(host: Any, events: Sequence[ReplaceableEvent],
                                       effects: Sequence[ReplacementEffect]
                                       ) -> tuple[PreparedCounterPrevention, ...]:
    """Remove once per applicable static instance across simultaneous sources."""
    grouped: dict[tuple[str, str], list[tuple[ReplacementEffect, tuple[str, ...]]]] = {}
    for effect in effects:
        if not effect.effect_id.startswith(HANDLER+':'):
            continue
        matching = tuple(event.event_id for event in events
            if effect.effect_id in event.applied_effects
            and effect.effect_id in (event.payload.get('prevention_applied') or {}))
        if not matching:
            continue
        object_id = effect.conditions['target_object_id']['eq']
        logical_id = effect.conditions['target_logical_object_id']['eq']
        grouped.setdefault((object_id, logical_id), []).append((effect, matching))
    results = []
    for (object_id, logical_id), instances in sorted(grouped.items()):
        effect = instances[0][0]
        event_ids = tuple(sorted({v for _, ids in instances for v in ids}))
        results.append(PreparedCounterPrevention(effect.effect_id, effect.source_id, event_ids,
            plan_counter_removal_effect(host, CounterRemoval(object_id, '+1/+1', len(instances),
                expected_logical_object_id=logical_id))))
    return tuple(results)


def validate_counter_prevention_results(host: Any, prepared: Sequence[PreparedCounterPrevention]) -> None:
    for result in prepared:
        validate_counter_removal_effect_plan(host, result.plan)


def validate_counter_prevention_snapshot(host: Any, prepared: Any) -> None:
    expected = prepare_counter_prevention_results(host, prepared.events, prepared.effects)
    if expected != prepared.aftermath.counter_removals:
        raise ValueError('Prepared prevention counter result does not match the applied source snapshot')


def validate_nested_counter_prevention(host: Any, aftermath: Any) -> Any:
    """Check the sequential removal tree against overlays before live damage commits."""
    validate_counter_prevention_results(host, aftermath.counter_removals)
    projected = projected_counter_prevention_host(host, aftermath.counter_removals)
    for instruction in aftermath.instructions:
        if instruction.nested_damage is not None:
            validate_counter_prevention_snapshot(projected, instruction.nested_damage)
            projected = validate_nested_counter_prevention(projected, instruction.nested_damage.aftermath)
    return projected


def commit_counter_prevention_results(host: Any, prepared: Sequence[PreparedCounterPrevention]
                                      ) -> tuple[CounterRemovalResult, ...]:
    validate_counter_prevention_results(host, prepared)
    results = []
    for result in prepared:
        removed = commit_counter_removal_effect(host, result.plan)
        results.append(removed)
        host._log(None, 'damage.prevention.counter_removal',
            f'{result.source_ref}: remove a +1/+1 counter after damage prevention',
            {'effect_id': result.effect_id, 'source': result.source_ref,
                'removed': removed.removed, 'damage_event_ids': list(result.event_ids)}, importance=2)
    return tuple(results)


def commit_immediate_counter_prevention(host: Any, prepared: Any):
    """Commit the validated CR615.5 result before ordinary damage-result mutation."""
    removed = commit_counter_prevention_results(host, prepared.counter_removals)
    return replace(prepared, counter_removals=()), tuple(r.object_id for r in removed if r.removed)
