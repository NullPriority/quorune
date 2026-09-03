from __future__ import annotations

from typing import Any, Iterable, Mapping

from .model import (
    ReplaceableEvent,
    ReplacementChoice,
    ReplacementEffect,
    ReplacementEffectError,
)
_COLLECTION_PREDICATES = {
    "contains",
    "contains_all",
    "contains_any",
    "contains_none",
}


def canonical_effects(
    effects: Iterable[ReplacementEffect],
) -> tuple[ReplacementEffect, ...]:
    values = tuple(effects)
    if any(not isinstance(value, ReplacementEffect) for value in values):
        raise ReplacementEffectError(
            "Replacement boundaries require typed effects"
        )
    effect_ids = [effect.effect_id for effect in values]
    if len(effect_ids) != len(set(effect_ids)):
        raise ReplacementEffectError(
            "Replacement effect IDs must be unique within an event boundary"
        )
    return tuple(sorted(values, key=lambda effect: effect.effect_id))


def event_field(event: ReplaceableEvent, field_name: str) -> Any:
    if field_name == "kind":
        return event.kind
    if field_name == "affected_player":
        return event.affected_player
    if field_name == "chooser":
        return event.chooser
    return event.payload.get(field_name)


def condition_matches(
    conditions: Mapping[str, Any], event: ReplaceableEvent
) -> bool:
    for field_name, expected in conditions.items():
        actual = event_field(event, field_name)
        if isinstance(expected, Mapping):
            supported = {
                "in",
                "not_in",
                "eq",
                "contains",
                "contains_all",
                "contains_any",
                "contains_none",
                "lt",
                "lte",
                "gt",
                "gte",
            }
            unsupported = sorted(str(value) for value in expected if value not in supported)
            if unsupported:
                raise ReplacementEffectError(
                    "Unsupported replacement condition predicate(s): "
                    + ", ".join(unsupported)
                )
            if not expected:
                raise ReplacementEffectError(
                    "Replacement condition predicates cannot be empty"
                )
            if actual is None and _COLLECTION_PREDICATES.intersection(expected):
                return False
            if "in" in expected and actual not in expected["in"]:
                return False
            if "not_in" in expected and actual in expected["not_in"]:
                return False
            if "eq" in expected and actual != expected["eq"]:
                return False
            if "contains" in expected and expected["contains"] not in (actual or ()):
                return False
            if "contains_all" in expected and not set(
                expected["contains_all"]
            ).issubset(set(actual or ())):
                return False
            if "contains_any" in expected and not set(
                expected["contains_any"]
            ).intersection(set(actual or ())):
                return False
            if "contains_none" in expected and set(
                expected["contains_none"]
            ).intersection(set(actual or ())):
                return False
            for predicate, comparison in (
                ("lt", lambda left, right: left < right),
                ("lte", lambda left, right: left <= right),
                ("gt", lambda left, right: left > right),
                ("gte", lambda left, right: left >= right),
            ):
                if predicate not in expected:
                    continue
                try:
                    matches = comparison(actual, expected[predicate])
                except TypeError as exc:
                    raise ReplacementEffectError(
                        f"Replacement condition {predicate} values are not comparable"
                    ) from exc
                if not matches:
                    return False
            continue
        if actual != expected:
            return False
    return True


def _deduplicate_explicit_application_groups(
    effects: Iterable[ReplacementEffect],
) -> list[ReplacementEffect]:
    """Collapse applicable sibling scopes only when explicitly grouped."""

    result: list[ReplacementEffect] = []
    seen: set[str] = set()
    for effect in effects:
        group_id = effect.application_group_id
        if group_id is None:
            result.append(effect)
            continue
        if group_id in seen:
            continue
        seen.add(group_id)
        result.append(effect)
    return result


def replacement_choice(
    event: ReplaceableEvent,
    effects: Iterable[ReplacementEffect],
) -> ReplacementChoice | None:
    applicable = _deduplicate_explicit_application_groups([
        effect
        for effect in canonical_effects(effects)
        if effect.event_kind == event.kind
        and effect.effect_id not in event.applied_effects
        and condition_matches(effect.conditions, event)
    ])
    if not applicable:
        return None
    selected_class = min(effect.replacement_class for effect in applicable)
    options = sorted(
        (
            effect
            for effect in applicable
            if effect.replacement_class == selected_class
        ),
        key=lambda effect: effect.effect_id,
    )
    return ReplacementChoice(
        event=event,
        chooser=event.chooser,
        options=tuple(effect.effect_id for effect in options),
        optional_options=tuple(
            effect.effect_id for effect in options if effect.optional
        ),
        replacement_class=selected_class,
    )
