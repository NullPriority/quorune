from __future__ import annotations

"""Capability closure for the closed ordinary Class level effect."""

from typing import Any, Iterable, Mapping, Sequence

from ..class_levels import (
    CLASS_LIFECYCLE_CAPABILITY_ID,
    CLASS_MECHANIC_ID,
)


def class_level_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        CLASS_MECHANIC_ID not in mechanics
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card", "level"}
        or effect.get("op") != "gain_class_level"
        or effect.get("card") != "$source.zone_object"
        or type(effect.get("level")) is not int
        or effect.get("level") not in {2, 3}
    ):
        return ()
    return (CLASS_LIFECYCLE_CAPABILITY_ID,)


__all__ = ["class_level_node_capabilities"]
