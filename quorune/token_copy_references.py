from __future__ import annotations

"""Current-or-immediate-LKI copy references behind the canonical zone hook."""

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .token_copy_recipes import TokenCopyRecipeError, TokenCopyRecipeSpec


COPY_REFERENCE_CONTEXT = "token_copy_references_v1"


def copy_recipes(effects: Sequence[Mapping[str, Any]]) -> tuple[TokenCopyRecipeSpec, ...]:
    """Visit executable wrappers, never copied future ability metadata."""
    found = []
    for effect in effects:
        if effect.get("op") == "create_token" and "copy_spec" in effect:
            found.append(TokenCopyRecipeSpec.from_dict(effect["copy_spec"]))
        for field in ("effects", "then_effects"):
            children = effect.get(field)
            if effect.get("op") in {"offer_optional_effect", "offer_optional_mana_payment"} and isinstance(children, (list, tuple)):
                found.extend(copy_recipes(children))
    return tuple(found)


def _snapshot(host: Any, card: Any) -> dict[str, Any]:
    from .token_creation import token_copy_snapshot
    if card.phased_out:
        raise TokenCopyRecipeError("Copy reference is phased out")
    if card.face_down:
        raise TokenCopyRecipeError("Face-down copiable characteristics are not represented by this recipe")
    return {"object_id": card.object_id, "logical_object_id": card.logical_object_id,
            "ref": card.ref, "zone": card.zone, "snapshot": token_copy_snapshot(host, card)}


def copy_source_context(host: Any, source: Any, effects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not any(recipe.origin == "source" for recipe in copy_recipes(effects)):
        return {}
    return {COPY_REFERENCE_CONTEXT: {"source": _snapshot(host, source)}}


def _identity(host: Any, item: Any, origin: str) -> tuple[str, str, str] | None:
    context = item.context.get(COPY_REFERENCE_CONTEXT)
    cached = context.get(origin) if isinstance(context, Mapping) else None
    if isinstance(cached, Mapping):
        return cached.get("object_id"), cached.get("logical_object_id"), cached.get("zone")
    if origin == "source":
        card = host.state.cards.get(item.source_object_id or item.card_object_id or "")
        expected = item.context.get("source_logical_object_id")
        if card is not None and card.logical_object_id == expected:
            return card.object_id, expected, "battlefield"
    if origin == "target" and len(item.targets) == 1:
        ref = item.targets[0]
        target = item.context.get("target_snapshots", {}).get(ref)
        card = next((value for value in host.state.cards.values() if value.ref == ref), None)
        if isinstance(target, Mapping) and card is not None and card.zone_change_counter == target.get("zone_change_counter"):
            return card.object_id, card.logical_object_id, "battlefield"
    if origin == "event_object":
        context = item.context.get("event_context", item.context)
        card = next((value for value in host.state.cards.values() if value.ref == context.get("card")), None)
        if card is not None and context.get("card_object_identity"):
            return card.object_id, context["card_object_identity"], "battlefield"
    return None


def copy_reference_snapshot(host: Any, item: Any, recipe: TokenCopyRecipeSpec) -> Mapping[str, Any]:
    identity = _identity(host, item, recipe.origin)
    if identity is None:
        raise TokenCopyRecipeError("Copy reference identity is unavailable")
    object_id, logical_object_id, zone = identity
    card = host.state.cards.get(object_id)
    if card is not None and card.logical_object_id == logical_object_id and card.zone == zone:
        value = _snapshot(host, card)
    else:
        value = item.context.get(COPY_REFERENCE_CONTEXT, {}).get(recipe.origin)
        if value is None and recipe.origin == "event_object":
            context = item.context.get("event_context", item.context)
            snapshot = context.get("copiable_snapshot")
            if isinstance(snapshot, Mapping):
                value = {"object_id": object_id, "logical_object_id": logical_object_id,
                         "ref": context["card"], "zone": zone, "snapshot": snapshot}
    if not isinstance(value, Mapping) or set(value) != {"object_id", "logical_object_id", "ref", "zone", "snapshot"}:
        raise TokenCopyRecipeError("Copy reference snapshot is unavailable or malformed")
    return deepcopy(dict(value))


def pin_copy_characteristic_departures(host: Any, cards: Sequence[Any], *, error_type=TokenCopyRecipeError) -> None:
    """Only evaluate references belonging to this committed departure group."""
    departing = {card.object_id: card for card in cards if card.zone == "battlefield"}
    if not departing:
        return
    updates = []
    try:
        for item in host.state.stack:
            program = host.semantics.get(item.semantic_key) if item.semantic_key else None
            if program is None:
                continue
            for recipe in copy_recipes(program.effects):
                identity = _identity(host, item, recipe.origin)
                if identity is None:
                    continue
                object_id, logical_object_id, zone = identity
                card = departing.get(object_id)
                if card is None or zone != "battlefield" or card.logical_object_id != logical_object_id:
                    continue
                updates.append((item, recipe.origin, _snapshot(host, card)))
        for item, origin, value in updates:
            item.context.setdefault(COPY_REFERENCE_CONTEXT, {})[origin] = value
    except (KeyError, TypeError, ValueError) as error:
        raise error_type(str(error)) from error
