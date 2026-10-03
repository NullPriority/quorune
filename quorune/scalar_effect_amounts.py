from __future__ import annotations

"""Read-only scalar evaluation and existing stack-context departure pinning."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .object_query import exact_numeric_characteristic
from .query_effect_amount_model import PublicQueryAmountError
from .scalar_effect_amount_model import (
    SCALAR_REFERENCE_CONTEXT, ScalarAmountOrigin, ScalarEffectAmountSpec, scalar_amount_specs,
)
from .turn_history import current_turn_history_events
from .drawing.restrictions import drawn_this_turn


class ScalarAmountHost(Protocol):
    state: Any
    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...
    def _type_parts(self, text: str) -> tuple[set[str], set[str], set[str]]: ...


def _snapshot(host, card, *, characteristics=None):
    data = dict(characteristics) if characteristics is not None else host._effective_card_data(card)
    types = host._type_parts(str(data.get("type_line") or ""))[0]
    has_stats = "creature" in types or card.zone != "battlefield"
    values = {field: (0 if "creature" not in types and data.get(field) is None else exact_numeric_characteristic(card, data, field)) if has_stats else 0
              for field in ("power", "toughness")}
    values["mana_value"] = data.get("mana_value", data.get("cmc"))
    return {"object_id": card.object_id, "logical_object_id": card.logical_object_id,
            "ref": card.ref, "zone": card.zone, **values}


def _current_card(host, ref):
    return next((card for card in host.state.cards.values() if card.ref == ref), None)


def scalar_source_context(host, source, effects, *, characteristics=None):
    """Seed source identity/LKI before activation costs or departure discovery."""
    if not any(spec.origin is ScalarAmountOrigin.SOURCE for spec in scalar_amount_specs(effects)):
        return {}
    return {SCALAR_REFERENCE_CONTEXT: {"source": _snapshot(host, source, characteristics=characteristics)}}


def _reference(host, item, spec):
    snapshots = item.context.setdefault(SCALAR_REFERENCE_CONTEXT, {})
    if not isinstance(snapshots, dict):
        raise PublicQueryAmountError("Scalar reference continuation is malformed")
    key = spec.origin.value
    if spec.origin is ScalarAmountOrigin.SOURCE:
        key = "source"
        initial = snapshots.get(key)
        if initial is None:
            card = host.state.cards.get(item.source_object_id or item.card_object_id or "")
            if card is None or card.logical_object_id != item.context.get("source_logical_object_id"):
                raise PublicQueryAmountError("Scalar source identity is unavailable")
            initial = _snapshot(host, card)
    elif spec.origin is ScalarAmountOrigin.TARGET:
        if len(item.targets) != 1 or item.targets[0] is None:
            raise PublicQueryAmountError("Scalar characteristic requires its single target")
        ref = item.targets[0]
        initial = snapshots.get(key)
        if initial is None:
            target = item.context.get("target_snapshots", {}).get(ref)
            card = _current_card(host, ref)
            if not isinstance(target, Mapping) or card is None or card.zone_change_counter != target.get("zone_change_counter"):
                raise PublicQueryAmountError("Scalar target identity is unavailable")
            initial = _snapshot(host, card)
    else:
        initial = snapshots.get(key)
        if initial is None:
            context = item.context.get("event_context", item.context)
            if not isinstance(context, Mapping):
                raise PublicQueryAmountError("Scalar event reference is unavailable")
            ref = context.get("card")
            identity = context.get("card_object_identity")
            card = _current_card(host, ref)
            if type(identity) is not str or not identity or card is None:
                raise PublicQueryAmountError("Scalar event object identity is unavailable")
            initial = {"ref": ref, "object_id": card.object_id,
                "logical_object_id": identity, "zone": "battlefield",
                "power": context.get("power"), "toughness": context.get("toughness"),
                "mana_value": context.get("mana_value")}
    if not isinstance(initial, Mapping) or set(initial) != {"ref", "object_id", "logical_object_id", "zone", "power", "toughness", "mana_value"}:
        raise PublicQueryAmountError("Scalar characteristic snapshot is malformed")
    snapshots[key] = dict(initial)
    card = host.state.cards.get(initial["object_id"])
    if card is not None and card.logical_object_id == initial["logical_object_id"] and card.zone == initial["zone"]:
        if card.phased_out:
            raise PublicQueryAmountError("Scalar characteristic object is phased out")
        return _snapshot(host, card)
    return initial


def pin_scalar_characteristic_departures(host, cards: Sequence[Any], *, error_type=PublicQueryAmountError):
    """Use the existing zone batch's predeparture checkpoint for pending reads."""
    try:
        by_ref = {card.ref: card for card in cards if card.zone == "battlefield"}
        updates = []
        for item in host.state.stack:
            program = host.semantics.get(item.semantic_key) if item.semantic_key else None
            if program is None:
                continue
            specs = scalar_amount_specs(program.effects)
            selected = [spec for spec in specs if spec.origin in {
                ScalarAmountOrigin.SOURCE, ScalarAmountOrigin.TARGET, ScalarAmountOrigin.EVENT_OBJECT}]
            for spec in selected:
                snapshot = _reference(host, item, spec)
                card = by_ref.get(snapshot["ref"])
                if card is None or card.logical_object_id != snapshot["logical_object_id"]:
                    continue
                key = "source" if spec.origin is ScalarAmountOrigin.SOURCE else spec.origin.value
                updates.append((item, key, _snapshot(host, card)))
        for item, key, value in updates:
            item.context.setdefault(SCALAR_REFERENCE_CONTEXT, {})[key] = value
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc


def _history_amount(host, item, fact):
    history = host.state.turn_history
    if history is None or history.schema_version != 1 or history.turn_sequence != host.state.turn_sequence:
        raise PublicQueryAmountError("Scalar turn history is unavailable")
    if fact == "cards_drawn":
        return drawn_this_turn(host, item.controller)
    kind = {"life_gained":"player_gained_life", "life_lost":"player_lost_life",
        "spells_cast":"spell_cast",
        "permanents_sacrificed":"permanent_sacrificed", "creatures_died":"creature_died",
        "creatures_entered":"permanent_entered"}[fact]
    events = current_turn_history_events(history, turn_sequence=host.state.turn_sequence, kind=kind)
    if fact in {"life_gained", "life_lost"}:
        return sum(event.amount for event in events if event.target == item.controller)
    if fact == "creatures_died":
        return len(events)
    if fact == "creatures_entered":
        return sum("creature" in event.types for event in events if event.actor == item.controller)
    return sum(event.actor == item.controller for event in events)


def resolve_scalar_effect_amount(host: ScalarAmountHost, value: Mapping[str, Any], item) -> int:
    spec = ScalarEffectAmountSpec.from_dict(value)
    cache = item.context.setdefault("declared_scalar_amounts", {}) if spec.binding_id else None
    identity = {"controller": item.controller, **spec.producer_identity}
    if cache is not None:
        if not isinstance(cache, dict):
            raise PublicQueryAmountError("Scalar declaration cache is malformed")
        previous = cache.get(spec.binding_id)
        if previous is not None:
            if not isinstance(previous, Mapping) or set(previous) != {"identity", "value"} or previous["identity"] != identity or type(previous["value"]) is not int or previous["value"] < 0:
                raise PublicQueryAmountError("Scalar declaration changed identity or value")
            return spec.coefficient * previous["value"]
    if spec.origin is ScalarAmountOrigin.EVENT_AMOUNT:
        context = item.context.get("event_context", item.context)
        amount = context.get("amount") if isinstance(context, Mapping) else None
    elif spec.origin is ScalarAmountOrigin.HISTORY:
        amount = _history_amount(host, item, spec.history_fact)
    else:
        amount = _reference(host, item, spec).get(spec.characteristic)
        if spec.characteristic == "mana_value" and type(amount) is float and amount.is_integer():
            amount = int(amount)
    if type(amount) is not int:
        raise PublicQueryAmountError("Scalar information is unavailable or malformed")
    # Ordinary result amounts, including +X modifiers, use CR 107.1b's zero.
    amount = max(0, amount)
    if cache is not None:
        cache[spec.binding_id] = {"identity": identity, "value": amount}
    return spec.coefficient * amount
