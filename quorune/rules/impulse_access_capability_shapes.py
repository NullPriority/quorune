from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..compiler.impulse_access_templates import (
    IMPULSE_ACCESS_CAPABILITY_ID,
    IMPULSE_ACCESS_MECHANIC_ID,
)


def fixed_impulse_access_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one fixed own-library impulse-access instruction."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        IMPULSE_ACCESS_MECHANIC_ID not in mechanics
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "player", "count", "duration"}
        or effect.get("op") != "fixed_impulse_access"
        or effect.get("player") != "$controller"
        or type(effect.get("count")) is not int
        or not 1 <= int(effect["count"]) <= 10
        or effect.get("duration")
        not in {"until_end_of_turn", "until_end_of_next_turn"}
    ):
        return ()
    return (IMPULSE_ACCESS_CAPABILITY_ID,)


__all__ = ["fixed_impulse_access_node_capabilities"]
