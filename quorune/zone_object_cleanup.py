from __future__ import annotations

"""One incarnation-filtered group through the canonical simultaneous move owner."""

from typing import Any, Mapping

from .errors import GameRuleError
from .zone_trigger_events import ZoneTransitionKind


def resolve_zone_object_cleanup_group(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> tuple[str, ...]:
    allowed = {
        "op",
        "cards",
        "from",
        "destination",
        "transition_kind",
        "required_controller",
        "reason",
        "_replacement_selections",
        "_runtime_source",
    }
    if operation != "move_if_in_zone" or set(effect) - allowed:
        raise GameRuleError("Grouped zone-object cleanup has a closed schema")
    raw_cards = effect.get("cards")
    expected_zone = effect.get("from")
    destination = effect.get("destination")
    required_controller = effect.get("required_controller")
    copy_exile = destination == "exile" and effect.get("transition_kind") is None and required_controller is None
    if (
        not isinstance(raw_cards, (list, tuple))
        or not raw_cards
        or expected_zone != "battlefield"
        or not (copy_exile or (destination == "graveyard" and effect.get("transition_kind") == "sacrifice" and required_controller == actor))
    ):
        raise GameRuleError(
            "Grouped zone-object sacrifice must use its trigger controller"
        )
    eligible: list[str] = []
    seen: set[str] = set()
    for raw in raw_cards:
        if (
            not isinstance(raw, Mapping)
            or set(raw)
            != {
                "card",
                "expected_zone_change_counter",
                "expected_object_identity",
            }
            or type(raw["card"]) is not str
            or not raw["card"]
            or type(raw["expected_zone_change_counter"]) is not int
            or raw["expected_zone_change_counter"] < 0
            or type(raw["expected_object_identity"]) is not str
            or not raw["expected_object_identity"]
            or raw["card"] in seen
        ):
            raise GameRuleError(
                "Grouped zone-object cleanup identities are malformed"
            )
        seen.add(raw["card"])
        try:
            card = host._resolve_object(actor, raw["card"])
        except GameRuleError:
            continue
        if (
            card.zone != expected_zone
            or card.phased_out
            or (required_controller is not None and card.controller != required_controller)
            or card.zone_change_counter
            != raw["expected_zone_change_counter"]
            or card.logical_object_id
            != raw["expected_object_identity"]
        ):
            continue
        eligible.append(card.ref)
    if not eligible:
        return ()
    raw_selections = effect.get("_replacement_selections", ())
    if not isinstance(raw_selections, (list, tuple)):
        raise GameRuleError(
            "Grouped zone-object cleanup replacements must be an array"
        )
    from .semantic_runtime.intents import MoveObjectsSimultaneouslyIntent

    try:
        intent = MoveObjectsSimultaneouslyIntent(
            actor=actor,
            object_refs=tuple(eligible),
            expected_zones=(expected_zone,),
            destination=destination,
            reason=reason,
            transition_kind=ZoneTransitionKind.ORDINARY if copy_exile else ZoneTransitionKind.SACRIFICE,
            controlled_only=not copy_exile,
            replacement_selections=tuple(raw_selections),
        )
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return host.move_objects_simultaneously_intent(intent)

