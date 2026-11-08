from __future__ import annotations

"""Capability closure for source-pinned permanent transformation."""

from typing import Any, Iterable, Mapping, Sequence

from ..spell_history_transform_model import SPELL_HISTORY_TRANSFORM_MECHANIC_ID


PERMANENT_TRANSFORM_CAPABILITY_ID = "permanent.transform.face_change"
TRIGGERED_SOURCE_TRANSFORM_MECHANIC = "fixed-triggered-source-transform"


def source_transform_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not {SPELL_HISTORY_TRANSFORM_MECHANIC_ID, "fixed-activated-source-transform",
             TRIGGERED_SOURCE_TRANSFORM_MECHANIC}.intersection(mechanics)
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if set(effect) != {
        "op",
        "card",
        "expected_transform_count",
    } or effect != {
        "op": "transform",
        "card": "$source.zone_object",
        "expected_transform_count": "$source.transform_count",
    }:
        return ()
    return (PERMANENT_TRANSFORM_CAPABILITY_ID,)


def triggered_source_transform_runtime_dependencies(program) -> tuple[str, ...]:
    if TRIGGERED_SOURCE_TRANSFORM_MECHANIC not in program.coverage:
        return ()
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..spell_history_transform_model import SPELL_HISTORY_TRANSFORM_COVERAGE
    from .fixed_effect_clause_shapes import fixed_optional_effect_node_capabilities

    if program.active_zone != "battlefield" or not {
        "fixed-typed-event-effect-trigger",
        CURRENT_ABILITY_FRAGMENT_COVERAGE,
        SPELL_HISTORY_TRANSFORM_COVERAGE,
    }.issubset(program.coverage):
        raise ValueError("Triggered source transformation requires its source and transform-count snapshots")
    effects = program.effects
    optional = len(effects) == 1 and effects[0].get("op") == "offer_optional_effect"
    required = source_transform_node_capabilities(
        effects=effects[0].get("effects", ()) if optional else effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Triggered source transformation requires one closed source instruction")
    if optional:
        wrapper = fixed_optional_effect_node_capabilities(
            effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        if not wrapper:
            raise ValueError("Optional source transformation requires its closed choice")
        return wrapper
    return required


__all__ = [
    "PERMANENT_TRANSFORM_CAPABILITY_ID",
    "TRIGGERED_SOURCE_TRANSFORM_MECHANIC",
    "triggered_source_transform_runtime_dependencies",
    "source_transform_node_capabilities",
]
