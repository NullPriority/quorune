from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Protocol

from ..entry_x_amount_model import ENTRY_X_AMOUNT_CAPABILITY, ENTRY_X_AMOUNT_KIND, EntryXAmountSpec
from ..impulse_access_model import (
    IMPULSE_ACCESS_DECLARED_COUNT_CAPABILITY, IMPULSE_ACCESS_DECLARED_COUNT_VERSION,
    IMPULSE_ACCESS_NEXT_END_STEP_CAPABILITY, ImpulseAccessDuration, impulse_access_count_is_legal,
)
from ..query_effect_amount_model import CAST_X_AMOUNT_KIND, DECLARED_AMOUNT_CAPABILITY, CastXAmountSpec

from ..compiler.impulse_access_templates import (
    IMPULSE_ACCESS_CAPABILITY_ID,
    IMPULSE_ACCESS_MECHANIC_ID,
)


def declared_impulse_access_dependencies(effect):
    if effect.get("op") != "fixed_impulse_access":
        return ()
    count = effect.get("count")
    if "schema_version" not in effect:
        if isinstance(count, Mapping):
            raise ValueError("A declared impulse count requires its instruction version")
        return ()
    if (set(effect) != {"op", "schema_version", "player", "count", "duration"}
            or type(effect["schema_version"]) is not int or effect["schema_version"] != IMPULSE_ACCESS_DECLARED_COUNT_VERSION
            or effect["player"] != "$controller"
            or effect["duration"] not in {"until_end_of_turn", "until_end_of_next_turn"}
            or not isinstance(count, Mapping)):
        raise ValueError("Declared impulse access requires a closed controller result")
    required = (IMPULSE_ACCESS_CAPABILITY_ID, IMPULSE_ACCESS_DECLARED_COUNT_CAPABILITY, DECLARED_AMOUNT_CAPABILITY)
    if count.get("kind") == CAST_X_AMOUNT_KIND and CastXAmountSpec.from_dict(count).coefficient == 1:
        return required
    if count.get("kind") == ENTRY_X_AMOUNT_KIND and EntryXAmountSpec.from_dict(count).coefficient == 1:
        return (*required, ENTRY_X_AMOUNT_CAPABILITY)
    raise ValueError("Impulse access count requires the unscaled cast or sealed entry X")


def next_end_step_impulse_dependencies(effect):
    if effect.get("op") != "fixed_impulse_access" or effect.get("duration") != ImpulseAccessDuration.NEXT_END_STEP.value:
        return ()
    if (set(effect) != {"op", "player", "count", "duration"} or effect["player"] != "$controller"
            or not impulse_access_count_is_legal(effect["count"], 1)):
        raise ValueError("Next-end-step impulse access requires one closed fixed-count result")
    return (IMPULSE_ACCESS_CAPABILITY_ID, IMPULSE_ACCESS_NEXT_END_STEP_CAPABILITY)


class FixedImpulseAccessProgram(Protocol):
    effects: tuple[Mapping[str, object], ...]
    target_schema: Mapping[str, object] | None
    coverage: tuple[str, ...]
    capability_dependencies: tuple[str, ...]


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
    try:
        if special := declared_impulse_access_dependencies(effect) or next_end_step_impulse_dependencies(effect):
            return special
    except (TypeError, ValueError):
        return ()
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


def is_closed_fixed_impulse_access_program(
    program: FixedImpulseAccessProgram,
) -> bool:
    """Recognize only fixed own-library temporary play-access programs."""

    required = set(
        fixed_impulse_access_node_capabilities(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
        )
    )
    return bool(required) and required.issubset(
        program.capability_dependencies
    )


__all__ = [
    "fixed_impulse_access_node_capabilities",
    "FixedImpulseAccessProgram",
    "is_closed_fixed_impulse_access_program",
]
