from __future__ import annotations

"""Closed nontargeted results for the sealed tap-state event controller."""

from typing import Any, Iterable, Mapping, Sequence


def tap_state_event_player_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]], target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = set(mechanic_ids)
    if "tap-state-event-player-result" not in mechanics or target_schema is not None or len(effects) != 1:
        return ()
    effect = effects[0]
    operation = effect.get("op")
    layouts = {
        "life": ("player", "delta", {"op", "player", "delta"}, ("life.change.effect",)),
        "lose_life": ("player", "amount", {"op", "player", "amount"}, ("life.change.effect",)),
        "mill": ("player", "count", {"op", "player", "count"}, ("zone.mill.fixed",)),
        "damage": ("target", "amount", {"op", "source", "target", "amount"}, ("damage.amount.positive", "damage.result.player_life")),
    }
    if operation not in layouts:
        return ()
    reference, amount, fields, capabilities = layouts[operation]
    if set(effect) != fields or effect.get(reference) != "$context.controller" or type(effect.get(amount)) is not int or effect[amount] <= 0:
        return ()
    if operation == "damage" and effect.get("source") != "$source":
        return ()
    return (*capabilities, "trigger.event.normalized_public_action")
