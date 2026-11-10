from __future__ import annotations

"""Capability closure for returning the source permanent to its owner."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.self_return_templates import FIXED_SELF_RETURN_MECHANIC


def fixed_self_return_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one mandatory, nontargeted source-permanent return."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        FIXED_SELF_RETURN_MECHANIC not in mechanics
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card"}
        or effect.get("op") != "bounce"
        or effect.get("card") not in ("$source", "$source.zone_object")
    ):
        return ()
    return ("permanent.return.owner_hand",)


__all__ = ["fixed_self_return_node_capabilities"]
