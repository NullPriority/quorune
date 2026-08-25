from __future__ import annotations

"""Canonical legality for activation object costs."""

from typing import Any, Mapping, Protocol

from .casting_additional_costs import fixed_zone_change_cost_candidates


class ActivationCostHost(Protocol):
    state: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...


def activation_choice_candidates(
    host: ActivationCostHost,
    actor: str,
    source: Any,
    choice: Any,
) -> tuple[str, ...]:
    """Return the canonical legal objects for one activation cost choice."""

    typed_cost = choice.fixed_zone_change_cost()
    if typed_cost is not None:
        return fixed_zone_change_cost_candidates(
            host,
            actor=actor,
            cost=typed_cost,
            exclude_object_id=(source.object_id if choice.another else None),
        )
    candidates: list[str] = []
    for object_id in host.state.players[actor].zones.get(choice.zone, []):
        card = host.state.cards[object_id]
        if choice.zone == "battlefield":
            if card.controller != actor or card.phased_out:
                continue
        elif card.owner != actor:
            continue
        if choice.another and card.object_id == source.object_id:
            continue
        if choice.card_type:
            type_line = str(
                host._effective_card_data(card).get("type_line") or ""
            ).casefold()
            if choice.card_type not in type_line:
                continue
        candidates.append(card.ref)
    return tuple(candidates)


__all__ = ["activation_choice_candidates"]
