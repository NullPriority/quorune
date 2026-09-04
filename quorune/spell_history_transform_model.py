from __future__ import annotations

"""Closed descriptor vocabulary for previous-turn self-transform triggers."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


SPELL_HISTORY_TRANSFORM_CAPABILITY_ID = (
    "trigger.upkeep.previous_turn_transform"
)
SPELL_HISTORY_TRANSFORM_CONDITION_FIELD = (
    "previous_turn_spell_transform_condition"
)
SPELL_HISTORY_TRANSFORM_MECHANIC_ID = (
    "fixed-previous-turn-spell-transform"
)
SPELL_HISTORY_TRANSFORM_COVERAGE = "source_transform_count_snapshot"


class PreviousTurnSpellCondition(str, Enum):
    NO_SPELLS = "no_spells"
    ONE_PLAYER_TWO_OR_MORE = "one_player_two_or_more"


class SpellHistoryTransformError(ValueError):
    """A previous-turn transform descriptor or runtime fact is malformed."""


@dataclass(frozen=True, slots=True)
class SpellHistoryTransformSpec:
    condition: PreviousTurnSpellCondition
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise SpellHistoryTransformError(
                "Unsupported spell-history transform schema version"
            )
        if not isinstance(self.condition, PreviousTurnSpellCondition):
            raise SpellHistoryTransformError(
                "Spell-history transform condition is invalid"
            )

    @property
    def template_id(self) -> str:
        return (
            "previous-turn-no-spells-self-transform-v1"
            if self.condition is PreviousTurnSpellCondition.NO_SPELLS
            else "previous-turn-player-two-spells-self-transform-v1"
        )

    def event_condition(self) -> dict[str, Any]:
        return {
            "field": SPELL_HISTORY_TRANSFORM_CONDITION_FIELD,
            "mode": self.condition.value,
            "op": "eq",
            "value": True,
        }

    def effect(self) -> dict[str, Any]:
        return {
            "op": "transform",
            "card": "$source.zone_object",
            "expected_transform_count": "$source.transform_count",
        }


__all__ = [
    "PreviousTurnSpellCondition",
    "SPELL_HISTORY_TRANSFORM_CAPABILITY_ID",
    "SPELL_HISTORY_TRANSFORM_CONDITION_FIELD",
    "SPELL_HISTORY_TRANSFORM_COVERAGE",
    "SPELL_HISTORY_TRANSFORM_MECHANIC_ID",
    "SpellHistoryTransformError",
    "SpellHistoryTransformSpec",
]
