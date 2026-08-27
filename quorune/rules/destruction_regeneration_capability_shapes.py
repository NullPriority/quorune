from __future__ import annotations

"""Closed capability shapes for destruction and regeneration effects."""

from typing import Any, Iterable, Mapping, Sequence

from ..affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    PermanentControllerRelation as AffectedControllerRelation,
)
from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceSpec,
)
from ..compiler.fixed_source_effect_sequences import SOURCE_ZONE_OBJECT
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed,
    direct_target_predicate_capabilities,
)


def targeted_destruction_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed direct destruction grammar."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not {"destroy", "cr-115-targets"}.issubset(mechanics)
        or len(effects) != 1
        or not direct_permanent_target_schema_is_closed(target_schema)
    ):
        return ()
    effect = effects[0]
    regeneration_prohibited = effect.get("regeneration_prohibited")
    expected_fields = {"op", "card"} | (
        {"regeneration_prohibited"}
        if regeneration_prohibited is True
        else set()
    )
    if (
        set(effect) != expected_fields
        or effect.get("op") != "destroy"
        or effect.get("card") != "$target.0"
        or ("regeneration-prohibition" in mechanics)
        is not (regeneration_prohibited is True)
    ):
        return ()
    assert target_schema is not None
    return (
        "permanent.destroy.effect",
        *(
            ("permanent.destroy.regeneration_prohibition",)
            if regeneration_prohibited is True
            else ()
        ),
        *direct_target_predicate_capabilities(target_schema),
        "target.revalidate_resolution",
    )


def self_regeneration_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one closed fixed-object regeneration action."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    contextual_mechanics = {
        value
        for value in mechanics
        if value.startswith("trigger-") or value.startswith("cr-603-")
    }
    effect_mechanics = mechanics - contextual_mechanics
    if len(effects) != 1:
        return ()
    effect = effects[0]
    if set(effect) != {"op", "card"} or effect.get("op") != "regenerate":
        return ()
    reference = effect.get("card")
    if target_schema is None and effect_mechanics == {"regenerate"}:
        if reference == SOURCE_ZONE_OBJECT:
            return ("permanent.regeneration.self_activation",)
        if not isinstance(reference, Mapping):
            return ()
        try:
            AttachmentReferenceSpec.from_dict(reference)
        except (AttachmentReferenceError, TypeError):
            return ()
        return (
            "permanent.regeneration.fixed_effect",
            "attachment.reference.current_or_lki",
        )
    if (
        effect_mechanics != {"regenerate", "cr-115-targets"}
        or reference != "$target.0"
        or not direct_permanent_target_schema_is_closed(target_schema)
    ):
        return ()
    assert target_schema is not None
    return (
        "permanent.regeneration.fixed_effect",
        *direct_target_predicate_capabilities(target_schema),
        "target.revalidate_resolution",
    )


def mass_destruction_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed fixed-set destruction grammar."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not {"destroy", "destroy-fixed-set"}.issubset(mechanics)
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    regeneration_prohibited = effect.get("regeneration_prohibited")
    expected_fields = {"op", "source", "set"} | (
        {"regeneration_prohibited"}
        if regeneration_prohibited is True
        else set()
    )
    if (
        set(effect) != expected_fields
        or effect.get("op") != "destroy_all"
        or effect.get("source") != "$source"
        or ("regeneration-prohibition" in mechanics)
        is not (regeneration_prohibited is True)
    ):
        return ()
    try:
        spec = AffectedPermanentSetSpec.from_dict(effect["set"])
    except (AffectedPermanentSetError, KeyError, TypeError):
        return ()
    targeted = spec.controller_relation is AffectedControllerRelation.TARGET_PLAYER
    schema = dict(target_schema or {})
    valid_player_schemas = (
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
            "player_relation": "any",
        },
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
            "player_relation": "opponent",
        },
    )
    prohibition_capability = (
        ("permanent.destroy.regeneration_prohibition",)
        if regeneration_prohibited is True
        else ()
    )
    if targeted:
        if "cr-115-targets" not in mechanics or schema not in valid_player_schemas:
            return ()
        if spec.target_controller != "$target.0":
            return ()
        return (
            "permanent.destroy.fixed_set",
            *prohibition_capability,
            "target.revalidate_resolution",
        )
    if target_schema is not None or "cr-115-targets" in mechanics:
        return ()
    return ("permanent.destroy.fixed_set", *prohibition_capability)


__all__ = [
    "mass_destruction_node_capabilities",
    "self_regeneration_node_capabilities",
    "targeted_destruction_node_capabilities",
]
