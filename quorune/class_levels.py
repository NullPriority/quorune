from __future__ import annotations

"""Typed fixed-mana Class level activations and lifecycle identity."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .abilities import ActivatedAbility
from .activation_condition_model import (
    ActivationCondition,
    ActivationConditionKind,
)
from .fixed_mana_abilities import MANA_COST_KEYS
from .replacement.immutable import FrozenMap, thaw_value
from .util import mana_cost_to_vector


CLASS_LIFECYCLE_CAPABILITY_ID = "permanent.class.fixed_lifecycle"
CLASS_LEVEL_ACTIVATION_HANDLER_ID = "ability.activated.class-level.v1"
CLASS_MECHANIC_ID = "cr-716-class-cards"
CLASS_REMINDER_TEXT = "(Gain the next level as a sorcery to add its ability.)"
_ABILITY_ID = re.compile(r"^ab[1-9][0-9]*$")
_ORDINARY_COST = re.compile(r"^(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+$")


class ClassLevelError(ValueError):
    """A Class level activation descriptor is malformed."""


@dataclass(frozen=True, slots=True)
class FixedClassLevelAbilitySpec:
    """One ordinary Class level-bar activation."""

    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_cost: FrozenMap
    level: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ClassLevelError(
                "Unsupported Class level activation schema version"
            )
        if (
            type(self.ability_id) is not str
            or _ABILITY_ID.fullmatch(self.ability_id) is None
        ):
            raise ClassLevelError("Class level ability ID must be abN")
        if type(self.line_index) is not int or self.line_index < 0:
            raise ClassLevelError(
                "Class level line index must be nonnegative"
            )
        if type(self.oracle_line) is not str or not self.oracle_line:
            raise ClassLevelError("Class level Oracle line must be nonempty")
        if (
            type(self.cost_text) is not str
            or _ORDINARY_COST.fullmatch(self.cost_text) is None
        ):
            raise ClassLevelError(
                "Class level costs require fixed ordinary mana symbols"
            )
        if not isinstance(self.mana_cost, FrozenMap):
            if not isinstance(self.mana_cost, Mapping):
                raise ClassLevelError("Class level mana cost must be an object")
            object.__setattr__(self, "mana_cost", FrozenMap(self.mana_cost))
        mana = thaw_value(self.mana_cost)
        expected, complex_symbols = mana_cost_to_vector(self.cost_text)
        if (
            set(mana) != set(MANA_COST_KEYS)
            or any(type(value) is not int or value < 0 for value in mana.values())
            or complex_symbols
            or mana != expected
        ):
            raise ClassLevelError(
                "Class level mana cost must match its printed fixed cost"
            )
        if type(self.level) is not int or self.level not in {2, 3}:
            raise ClassLevelError("Class activations support levels 2 and 3")

    @property
    def required_current_level(self) -> int:
        return self.level - 1

    def to_activated_ability(self) -> ActivatedAbility:
        return ActivatedAbility(
            ability_id=self.ability_id,
            line_index=self.line_index,
            oracle_line=self.oracle_line,
            cost_text=self.cost_text,
            effect_text=f"Level {self.level}",
            zones=("battlefield",),
            mana=thaw_value(self.mana_cost),
            sorcery_speed=True,
            activation_conditions=(
                ActivationCondition(
                    ActivationConditionKind.CLASS_LEVEL_EQUALS,
                    minimum=self.required_current_level,
                ),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ability_id": self.ability_id,
            "line_index": self.line_index,
            "oracle_line": self.oracle_line,
            "cost_text": self.cost_text,
            "mana_cost": thaw_value(self.mana_cost),
            "level": self.level,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "FixedClassLevelAbilitySpec":
        expected = {
            "schema_version",
            "ability_id",
            "line_index",
            "oracle_line",
            "cost_text",
            "mana_cost",
            "level",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ClassLevelError(
                "Class level activations use a closed schema"
            )
        mana_cost = value["mana_cost"]
        if not isinstance(mana_cost, Mapping):
            raise ClassLevelError("Class level mana cost must be an object")
        return cls(
            schema_version=value["schema_version"],
            ability_id=value["ability_id"],
            line_index=value["line_index"],
            oracle_line=value["oracle_line"],
            cost_text=value["cost_text"],
            mana_cost=FrozenMap(mana_cost),
            level=value["level"],
        )


def class_level_handler_descriptor(
    spec: FixedClassLevelAbilitySpec,
) -> dict[str, Any]:
    return {
        "handler_id": CLASS_LEVEL_ACTIVATION_HANDLER_ID,
        "schema_version": 1,
        "event": "activate",
        "ability": spec.to_dict(),
    }


__all__ = [
    "CLASS_LEVEL_ACTIVATION_HANDLER_ID",
    "CLASS_LIFECYCLE_CAPABILITY_ID",
    "CLASS_MECHANIC_ID",
    "CLASS_REMINDER_TEXT",
    "ClassLevelError",
    "FixedClassLevelAbilitySpec",
    "class_level_handler_descriptor",
]
