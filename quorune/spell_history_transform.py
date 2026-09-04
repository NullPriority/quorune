from __future__ import annotations

"""Runtime previous-turn predicates for self-transform upkeep triggers."""

from typing import Mapping

from .model import CardInstance, TurnHistory
from .spell_history_transform_model import (
    PreviousTurnSpellCondition,
    SPELL_HISTORY_TRANSFORM_CAPABILITY_ID,
    SPELL_HISTORY_TRANSFORM_CONDITION_FIELD,
    SPELL_HISTORY_TRANSFORM_COVERAGE,
    SPELL_HISTORY_TRANSFORM_MECHANIC_ID,
    SpellHistoryTransformError,
    SpellHistoryTransformSpec,
)
from .turn_history import previous_turn_spell_cast_counts


def spell_history_transform_condition_holds(
    history: TurnHistory | None,
    *,
    current_turn_sequence: int,
    source: CardInstance,
    context: Mapping[str, Any],
    mode: object,
) -> bool:
    """Evaluate one trigger-time or resolution-time intervening condition."""

    try:
        condition = PreviousTurnSpellCondition(str(mode))
    except ValueError as exc:
        raise SpellHistoryTransformError(
            "Unknown previous-turn spell condition"
        ) from exc
    if (
        source.zone != "battlefield"
        or source.phased_out
        or context.get("phase") != "beginning"
        or context.get("step") != "upkeep"
    ):
        return False
    expected_identity = context.get("source_logical_object_id")
    if expected_identity is not None:
        if type(expected_identity) is not str or not expected_identity:
            raise SpellHistoryTransformError(
                "Source logical identity must be a nonempty string"
            )
        if source.logical_object_id != expected_identity:
            return False
    counts = previous_turn_spell_cast_counts(
        history,
        current_turn_sequence=current_turn_sequence,
    )
    if counts is None:
        return False
    if condition is PreviousTurnSpellCondition.NO_SPELLS:
        return sum(counts.values()) == 0
    return any(count >= 2 for count in counts.values())


__all__ = [
    "PreviousTurnSpellCondition",
    "SPELL_HISTORY_TRANSFORM_CAPABILITY_ID",
    "SPELL_HISTORY_TRANSFORM_CONDITION_FIELD",
    "SPELL_HISTORY_TRANSFORM_COVERAGE",
    "SPELL_HISTORY_TRANSFORM_MECHANIC_ID",
    "SpellHistoryTransformError",
    "SpellHistoryTransformSpec",
    "spell_history_transform_condition_holds",
]
