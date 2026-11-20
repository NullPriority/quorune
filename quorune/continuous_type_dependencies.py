from __future__ import annotations

"""Per-object CR613.8 applicability dependencies for fixed type setters."""

from copy import deepcopy
from dataclasses import dataclass, replace
from itertools import groupby
from .continuous_effect_model import ContinuousEffectDuration, Layer
from .basic_land_types import BASIC_LAND_MANA


def bind_type_applicability_dependencies(effects,base,context):
    from .continuous_effects import _matches,_apply_operation
    values=tuple(effects);result=[]
    def bound_to_object(effect):
        related=effect.related_object
        if related is not None and (related.object_id!=context.get('object_id') or related.logical_object_id!=context.get('logical_object_id')):return False
        if effect.locked_objects and not any(i.object_id==context.get('object_id') and i.logical_object_id==context.get('logical_object_id') for i in effect.locked_objects):return False
        return True
    for effect in values:
        if (effect.layer is not Layer.TYPE or effect.applies.types_all!=('land',) or not effect.applies.subtypes_any
                or not set(effect.applies.subtypes_any)<=set(BASIC_LAND_MANA)):
            result.append(effect);continue
        original=_matches(effect.applies,base,context);dependencies=set(effect.depends_on)
        for candidate in values:
            if candidate.effect_id==effect.effect_id or candidate.layer is not Layer.TYPE or candidate.sublayer!=effect.sublayer:
                continue
            if not any(op.op in {'set_basic_land_types','set_types','add_types','remove_types'} for op in candidate.operations):continue
            if not bound_to_object(candidate) or not _matches(candidate.applies,base,context):continue
            changed=deepcopy(base)
            for operation in candidate.operations:_apply_operation(changed,operation,context)
            if _matches(effect.applies,changed,context)!=original:dependencies.add(candidate.effect_id)
        result.append(replace(effect,depends_on=tuple(sorted(dependencies))))
    return tuple(result)

def _type_effect_matches_object(effect, base, context):
    from .continuous_effects import _matches

    identity = context.get("object_id"), context.get("logical_object_id")
    related = effect.related_object
    if related is not None and (related.object_id, related.logical_object_id) != identity:
        return False
    if effect.locked_objects and not any(
        (item.object_id, item.logical_object_id) == identity for item in effect.locked_objects
    ):
        return False
    return _matches(effect.applies, base, context)


def _current_type_dependencies(remaining, base, context):
    from .continuous_effects import _apply_operation

    matches = {key: _type_effect_matches_object(effect, base, context) for key, effect in remaining.items()}
    dependencies = {key: set(effect.depends_on).intersection(remaining) for key, effect in remaining.items()}
    for earlier_id, earlier in remaining.items():
        if not matches[earlier_id]:
            continue
        preview = deepcopy(base)
        for operation in earlier.operations:
            _apply_operation(preview, operation, context)
        for later_id, later in remaining.items():
            if earlier_id != later_id and earlier.characteristic_defining == later.characteristic_defining:
                if _type_effect_matches_object(later, preview, context) != matches[later_id]:
                    dependencies[later_id].add(earlier_id)
    return dependencies


def _dependency_cycle_ready(dependencies, order_key):
    """Return one dependency loop whose outside prerequisites have all applied."""
    def reachable(start):
        seen = {start}
        pending = [start]
        while pending:
            for child in dependencies[pending.pop()] - seen:
                seen.add(child)
                pending.append(child)
        return seen

    paths = {key: reachable(key) for key in dependencies}
    for key in sorted(dependencies, key=order_key):
        component = {peer for peer in paths[key] if key in paths[peer]}
        if not any(dependencies[peer] - component for peer in component):
            return tuple(sorted(component, key=order_key))
    raise AssertionError("A finite type-dependency graph has a terminal component")


def _live_type_effect_order(effects, dependencies_for, cycles):
    """Apply one graph step at a time, rebuilding dependencies against the new state."""
    remaining = {effect.effect_id: effect for effect in effects}
    def order_key(key):
        effect = remaining[key]
        return not effect.characteristic_defining, effect.timestamp, key

    ignored = set()
    while remaining:
        dependencies = dependencies_for(remaining)
        for key, depends in dependencies.items():
            depends.difference_update(prerequisite for prerequisite in tuple(depends) if (key, prerequisite) in ignored)
        ready = [key for key, depends in dependencies.items() if not depends]
        if not ready:
            cycle = _dependency_cycle_ready(dependencies, order_key)
            cycles.append(cycle)
            ignored.update((key, peer) for key in cycle for peer in cycle)
            ready = list(cycle)
        yield remaining.pop(min(ready, key=order_key))


@dataclass(frozen=True, slots=True)
class TypeEffectOrder:
    effect_ids: tuple[str, ...]
    dependency_cycles: tuple[tuple[str, ...], ...]


def battlefield_type_effect_order(views):
    """Order type effects by their dependency on any public recipient."""
    from .continuous_effects import _apply_operation

    rows = []
    all_effects = {}
    for base, context, effects in views:
        local = {effect.effect_id: effect for effect in effects
                 if effect.layer is Layer.TYPE and (effect.source_present or effect.duration
                     is not ContinuousEffectDuration.WHILE_SOURCE_PRESENT)}
        all_effects.update(local)
        rows.append((base, context, local))

    def dependencies_for(remaining):
        result = {key: set(effect.depends_on).intersection(remaining)
                  for key,effect in remaining.items()}
        for base, context, local in rows:
            dependencies = _current_type_dependencies(
                {key: effect for key, effect in local.items() if key in remaining}, base, context,
            )
            for key, prerequisites in dependencies.items():
                result[key].update(prerequisites)
        return result

    cycles = []
    ordered = []
    for effect in _live_type_effect_order(all_effects.values(), dependencies_for, cycles):
        ordered.append(effect.effect_id)
        for base, context, local in rows:
            if effect.effect_id in local and _type_effect_matches_object(effect, base, context):
                for operation in effect.operations:
                    _apply_operation(base, operation, context)
    return TypeEffectOrder(tuple(ordered), tuple(cycles))


def effects_with_live_type_recipient_scopes(ordered, present, base, context, cycles, board_order=None):
    """Retain the existing order except where a new grouped recipient can participate."""
    from .continuous_effects import _matches

    if not any(effect.recipient_scope_anchor is not None for effect in present):
        yield from ordered
        return
    for layer, grouped in groupby(ordered, key=lambda effect: effect.layer):
        group = tuple(grouped)
        if layer is Layer.TYPE and board_order is not None:
            by_id = {effect.effect_id: effect for effect in group}
            cycles[:] = [cycle for cycle in cycles if not set(cycle).issubset(by_id)]
            cycles.extend(cycle for cycle in board_order.dependency_cycles if set(cycle).intersection(by_id))
            for key in board_order.effect_ids:
                if key in by_id:
                    yield by_id.pop(key)
            yield from by_id.values()
            continue
        if layer is not Layer.TYPE or not any(
            effect.recipient_scope_anchor is not None
            and _matches(replace(effect.applies, types_all=()), base, context)
            for effect in group
        ):
            yield from group
            continue
        # Discard dependencies inferred by the historical one-pass type helper.
        type_ids = {effect.effect_id for effect in group}
        cycles[:] = [cycle for cycle in cycles if not set(cycle).issubset(type_ids)]
        yield from _live_type_effect_order(
            tuple(effect for effect in present if effect.layer is Layer.TYPE),
            lambda remaining: _current_type_dependencies(remaining, base, context), cycles,
        )
