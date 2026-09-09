from __future__ import annotations

"""Strict capability closure for source attachment instructions."""

from typing import Any, Iterable, Mapping, Sequence

from .attachment_actions import (
    FIXED_SOURCE_ATTACHMENT_CAPABILITY,
    FOR_MIRRODIN_CAPABILITY,
    LIVING_WEAPON_CAPABILITY,
)


def attachment_action_covered_mechanics(
    supplied: Iterable[str],
) -> tuple[str, ...]:
    """Return only mechanics backed by the supplied attachment capabilities."""

    capabilities = frozenset(supplied)
    mechanics: list[str] = []
    if capabilities.intersection(
        {"attachment.aura.simple_object", "attachment.aura.typed_restriction"}
    ):
        mechanics.append("enchant")
    if FIXED_SOURCE_ATTACHMENT_CAPABILITY in capabilities:
        mechanics.append("cr-701-3-attach")
    if LIVING_WEAPON_CAPABILITY in capabilities:
        mechanics.append("living weapon")
    if FOR_MIRRODIN_CAPABILITY in capabilities:
        mechanics.append("for mirrodin!")
    return tuple(mechanics)


def fixed_attachment_action_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        "cr-701-3-attach" not in mechanics
        or len(effects) != 1
        or not isinstance(target_schema, Mapping)
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "attachment_kind", "source", "target"}
        or effect.get("op") != "attach"
        or effect.get("attachment_kind") not in {"aura", "equipment"}
        or effect.get("source") != "$source.zone_object"
        or effect.get("target") != "$target.0"
    ):
        return ()
    allowed_schema = {
        "zones",
        "categories",
        "controller",
        "creature",
        "count",
        "predicate",
    }
    if (
        set(target_schema) - allowed_schema
        or target_schema.get("zones") != ["battlefield"]
        or target_schema.get("categories") != ["permanent"]
        or target_schema.get("creature") is not True
        or target_schema.get("count") != 1
        or target_schema.get("controller") not in {None, "you"}
        or target_schema.get("predicate")
        not in {None, "not_source_attachment"}
    ):
        return ()
    if effect.get("attachment_kind") == "equipment" and (
        target_schema.get("controller") != "you"
        or target_schema.get("predicate") is not None
    ):
        return ()
    return (
        FIXED_SOURCE_ATTACHMENT_CAPABILITY,
        "target.revalidate_resolution",
    )


__all__ = [
    "attachment_action_covered_mechanics",
    "fixed_attachment_action_node_capabilities",
]
