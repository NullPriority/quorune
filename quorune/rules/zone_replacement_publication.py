from __future__ import annotations

"""Publish the effects of a committed zone-replacement journal."""

from typing import TYPE_CHECKING, Any, Sequence

from ..replacement.composition_scope import card_only_replacement, card_component_event
from .zone_replacement_token_trigger import publish_committed_replacement_token_trigger

if TYPE_CHECKING:
    from ..semantic_runtime.zone_replacements import ZoneReplacementHost
    from ..semantic_runtime.zone_replacement_model import PreparedZoneChange


def log_applied_zone_replacements(
    host: ZoneReplacementHost,
    prepared: PreparedZoneChange,
    card: Any,
    *,
    requested_destination: str,
    error_type: type[Exception],
    visibility: Sequence[str] | None = None,
) -> None:
    """Emit audit events and their retained consequences after the movement."""

    effect_by_id = {
        effect.effect_id: effect for effect in prepared.effects
    }
    for selection in prepared.journal:
        selected_id = str(selection.effect_id or "")
        if selected_id.startswith("decline:"):
            continue
        replacement = effect_by_id.get(selected_id)
        if replacement is None:
            raise error_type(
                "Applied zone replacement is absent from its source snapshot"
            )
        if replacement.event_kind != "zone.change":
            continue
        publish_committed_replacement_token_trigger(host, prepared, replacement, card)
        scoped_event=card_component_event(prepared.event) if card_only_replacement(replacement) else None
        scoped_counters=tuple(scoped_event.children) if scoped_event is not None else prepared.counter_events
        host._log(
            None,
            "replacement.apply",
            (
                f"{replacement.source_id} replaced the zone change for "
                f"{card.ref}."
            ),
            {
                "source": replacement.source_id,
                "effect_id": replacement.effect_id,
                "object": card.ref,
                "replaced_destination": requested_destination,
                "destination": scoped_event.payload['destination'] if scoped_event is not None else card.zone,
                **({'component_scope':'cards'} if scoped_event is not None else {}),
                "counters": [
                    {
                        "name": str(
                            event.payload.get("counter_name") or ""
                        ),
                        "amount": int(event.payload.get("amount", 0)),
                    }
                    for event in scoped_counters
                    if event.payload.get("source")
                    == replacement.source_id
                ],
            },
            importance=2,
            changed_objects=[card.object_id],
            **({"visibility": visibility} if visibility is not None else {}),
        )
