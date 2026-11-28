from __future__ import annotations

"""Complete the ordered choices of an intrinsic basic land entry replacement."""

from collections import Counter
from dataclasses import replace

from ..basic_land_types import BASIC_LAND_MANA, LAND_SUBTYPES
from ..entry_designations import (
    BASIC_LAND_ENTRY_HANDLER_ID,
    BASIC_LAND_ENTRY_LIFE_FIELD,
    BASIC_LAND_ENTRY_PAID_FIELD,
    EntryDesignationKind,
    validate_designation,
)
from .model import ReplacementEffectError
from .operations import AddAmount, SetField


_TYPE_FIELD = EntryDesignationKind.BASIC_LAND_TYPE.event_field
_FIELDS = frozenset({_TYPE_FIELD, BASIC_LAND_ENTRY_LIFE_FIELD, BASIC_LAND_ENTRY_PAID_FIELD})


def pending_basic_land_payment(event):
    return (
        event.kind == "zone.change"
        and event.payload.get("destination") == "battlefield"
        and event.payload.get(_TYPE_FIELD) is not None
        and event.payload.get(BASIC_LAND_ENTRY_PAID_FIELD) is None
    )


def is_basic_land_payment(effect):
    return any(
        isinstance(operation, SetField) and operation.field == BASIC_LAND_ENTRY_PAID_FIELD
        for operation in effect.operations
    )


def available_basic_land_payments(batch, effects):
    payments = tuple(effect for effect in effects if is_basic_land_payment(effect))
    if not payments:
        return effects
    by_ref = {}
    committed = Counter()
    for event in batch.events:
        if event.kind == "zone.change" and event.payload.get("destination") == "battlefield":
            by_ref[event.payload.get("object_ref")] = event
            committed[event.payload.get("destination_controller")] += event.payload.get("entry_life_payment", 0)
    substitutions = {}
    for effect in payments:
        event = by_ref.get(effect.source_id)
        if event is None or not pending_basic_land_payment(event) or not effect.optional:
            continue
        charge = next(operation.amount for operation in effect.operations if isinstance(operation, AddAmount))
        player = event.payload["destination_controller"]
        if event.payload[BASIC_LAND_ENTRY_LIFE_FIELD] - committed[player] < charge:
            substitutions[effect.effect_id] = replace(
                effect, optional=False, operations=effect.decline_operations, decline_operations=(),
                label=effect.decline_label, decline_label=None,
            )
    return tuple(substitutions.get(effect.effect_id, effect) for effect in effects)


def validate_basic_land_entry_field(event, payload, operation, effect_id):
    field = operation.field
    if field not in _FIELDS:
        return
    object_ref = payload.get("object_ref")
    if (event.kind != "zone.change" or payload.get("destination") != "battlefield"
            or payload.get("origin") == "battlefield" or payload.get(field) is not None
            or not object_ref or f"{BASIC_LAND_ENTRY_HANDLER_ID}:{object_ref}:" not in effect_id):
        raise ReplacementEffectError("Basic land entry must belong to its original entering object")
    if field == _TYPE_FIELD:
        try:
            subtype = validate_designation(EntryDesignationKind.BASIC_LAND_TYPE, operation.value)
        except ValueError as exc:
            raise ReplacementEffectError(str(exc)) from exc
        current = payload["object_types"]
        payload["object_types"] = sorted(
            {value for value in current if value.casefold().replace("’", "'") not in LAND_SUBTYPES}
            | {subtype}
        )
    if field == BASIC_LAND_ENTRY_LIFE_FIELD and (
        payload.get(_TYPE_FIELD) not in BASIC_LAND_MANA
        or type(operation.value) is not int or operation.value < 0
    ):
        raise ReplacementEffectError("Basic land entry requires its captured life payment limit")
    if field == BASIC_LAND_ENTRY_PAID_FIELD and (
        payload.get(_TYPE_FIELD) not in BASIC_LAND_MANA or operation.value is not True
    ):
        raise ReplacementEffectError("Basic land entry life decision must follow its original type choice")
