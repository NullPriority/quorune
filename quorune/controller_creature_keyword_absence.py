from __future__ import annotations

"""Closed live creature keyword predicate for one ordinary end-step trigger."""

from dataclasses import dataclass
from typing import Any, Mapping


CONTROLLER_CREATURE_KEYWORD_ABSENCE_KIND = "controller_creature_keyword_absence"
CONTROLLER_CREATURE_KEYWORD_ABSENCE_COVERAGE = "controller_end_step_creature_keyword_absence_v1"


@dataclass(frozen=True, slots=True)
class ControllerCreatureKeywordAbsence:
    keyword: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.keyword) is not str or self.keyword != "decayed":
            raise ValueError("Controller creature keyword absence requires the closed Decayed predicate")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported controller creature keyword absence version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": CONTROLLER_CREATURE_KEYWORD_ABSENCE_KIND,
            "keyword": self.keyword,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ControllerCreatureKeywordAbsence:
        if (not is_controller_creature_keyword_absence(value)
                or set(value) != {"schema_version", "kind", "keyword"}):
            raise ValueError("Controller creature keyword absence requires its closed descriptor")
        return cls(value["keyword"], value["schema_version"])


def is_controller_creature_keyword_absence(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("kind") == CONTROLLER_CREATURE_KEYWORD_ABSENCE_KIND


def is_controller_end_step_schedule(condition: Any) -> bool:
    if not isinstance(condition, Mapping) or set(condition) != {"all"}:
        return False
    clauses = condition["all"]
    return isinstance(clauses, (list, tuple)) and tuple(clauses) == (
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "end_step"},
    )
