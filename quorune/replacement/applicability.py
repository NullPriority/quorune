from __future__ import annotations

from typing import Any, Iterable, Mapping

from .model import (
    COUNTER_PLACEMENT_ROUNDED_TO_ZERO_FIELD,
    COUNTER_REPLACEMENT_GENERATED_FIELD,
    ReplaceableEvent,
    ReplacementChoice,
    ReplacementEffect,
    ReplacementEffectError,
)
from .damage_redirection import DAMAGE_RECIPIENT_TYPES_FIELD

COUNTER_RECIPIENT_TYPES_FIELD = "counter_recipient_types_v1"
_RECIPIENT_TYPE_EVENTS = {COUNTER_RECIPIENT_TYPES_FIELD: "counter.place", DAMAGE_RECIPIENT_TYPES_FIELD: "damage"}

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
    if field_name == 'zone_object_qualifications_v1':
        from ..zone_object_qualifications import zone_object_qualifications_v1
        try:return zone_object_qualifications_v1(event)
        except (ValueError,TypeError) as exc:raise ReplacementEffectError(str(exc)) from exc
    if field_name in _RECIPIENT_TYPE_EVENTS:
        if event.kind != _RECIPIENT_TYPE_EVENTS[field_name]:
            return ()
        kind = event.payload.get("target_kind")
        if kind == "player":
            return ("player",)
        types = event.payload.get("target_types")
        if kind != "permanent" or not isinstance(types, (tuple, list)):
            return ()
        return tuple(value for value in types if value != "player")
    if field_name == "kind":
        return event.kind
    if field_name == "affected_player":
        return event.affected_player
    if field_name == "chooser":
        return event.chooser
    if field_name == "effect_generated" and event.payload.get(COUNTER_REPLACEMENT_GENERATED_FIELD) is True:
        amount = event.payload.get("amount")
        return type(amount) is int and amount > 0
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
    from ..counter_permission_events import counter_event_is_prohibited
    if event.payload.get(COUNTER_PLACEMENT_ROUNDED_TO_ZERO_FIELD) is True or counter_event_is_prohibited(event):
        return None
    values=canonical_effects(effects)
    from .amplify import AmplifyEntryOperation
    from .operations import GraveyardCopyEntryOperation, LinkedGraveyardEntryOperation
    from .composition_scope import replacement_condition_event
    from ..rules.entry_land_discard import entry_land_discard_applicable
    def matches(effect):
        try:return condition_matches(effect.conditions,replacement_condition_event(event,effect))
        except (ValueError,TypeError,KeyError) as exc:raise ReplacementEffectError(str(exc)) from exc
    used_occurrences={effect.occurrence_id for effect in values if effect.effect_id in event.applied_effects and effect.occurrence_id is not None}
    applicable = _deduplicate_explicit_application_groups([
        effect
        for effect in values
        if effect.event_kind == event.kind
        and effect.effect_id not in event.applied_effects
        and (effect.occurrence_id is None or effect.occurrence_id not in used_occurrences)
        and matches(effect)
        and entry_land_discard_applicable(event, effect.operations)
        and all(not isinstance(operation,GraveyardCopyEntryOperation) or len(operation.eligible_cards(event)) >= 2
                for operation in effect.operations)
        and all(not isinstance(operation, LinkedGraveyardEntryOperation) or operation.eligible_cards(event)
                for operation in effect.operations)
        and (event.kind != "zone.change" or all(
            not isinstance(operation, AmplifyEntryOperation) or operation.eligible_cards(event)
            for operation in effect.operations
        ))
    ])
    from .chosen_basic_land_entry import is_basic_land_payment, pending_basic_land_payment

    if pending_basic_land_payment(event):
        applicable = [effect for effect in applicable if is_basic_land_payment(effect)]
        if not applicable:
            raise ReplacementEffectError("A chosen basic land type lost its entry payment decision")
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
    from .face_status_copy import FACE_UP_COPY_EVENT

    optional = tuple(effect.effect_id for effect in options if effect.optional)
    return ReplacementChoice(
        event=event,
        chooser=event.chooser,
        options=tuple(effect.effect_id for effect in options),
        optional_options=optional[:1] if event.kind == FACE_UP_COPY_EVENT else optional,
        replacement_class=selected_class,
    )
