from __future__ import annotations

"""A Decayed predicate over the normalized facts of one creature death."""

from dataclasses import dataclass
from typing import Any, Mapping

from .creature_subtypes import canonical_creature_subtype


DEATH_KEYWORD_ABSENCE_KIND = "death_keyword_absence"
DEATH_KEYWORD_ABSENCE_COVERAGE = "controlled_creature_death_keyword_absence_v1"


@dataclass(frozen=True, slots=True)
class DeathKeywordAbsence:
    subtype: str
    keyword: str = "decayed"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.subtype) is not str or canonical_creature_subtype(self.subtype) != self.subtype:
            raise ValueError("Death keyword absence requires one canonical creature subtype")
        if type(self.keyword) is not str or self.keyword != "decayed":
            raise ValueError("Death keyword absence requires the closed Decayed predicate")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported death keyword absence version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": DEATH_KEYWORD_ABSENCE_KIND,
            "subtype": self.subtype,
            "keyword": self.keyword,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DeathKeywordAbsence:
        if not is_death_keyword_absence(value) or set(value) != {"schema_version", "kind", "subtype", "keyword"}:
            raise ValueError("Death keyword absence requires its closed descriptor")
        return cls(value["subtype"], value["keyword"], value["schema_version"])

    def occurrence_condition(self) -> dict[str, Any]:
        return {"all": [
            {"field": "subtypes", "op": "contains_any", "value": [self.subtype]},
            {"field": "previous_controller", "op": "eq", "value": "$source.controller"},
            {"field": "card", "op": "ne", "value": "$source.ref"},
        ]}

    def holds(self, context: Mapping[str, Any], *, controller: str) -> bool:
        if (context.get("event") not in (None, "creature.dies")
                or context.get("from") != "battlefield" or context.get("to") != "graveyard"
                or type(controller) is not str or not controller
                or context.get("previous_controller") != controller
                or type(context.get("card")) is not str or not context["card"]):
            raise ValueError("Death keyword absence lost its original controlled departure")
        identity = context.get("card_object_identity")
        object_id, separator, incarnation = identity.rpartition("@") if type(identity) is str else ("", "", "")
        if not object_id or not separator or not incarnation.isdecimal():
            raise ValueError("Death keyword absence lost the departed incarnation")
        types, subtypes, keywords = (context.get(field) for field in ("types", "subtypes", "keywords"))
        if any(not isinstance(facts, (list, tuple)) or any(
            type(value) is not str or not value or value.casefold() != value for value in facts
        ) for facts in (types, subtypes, keywords)):
            raise ValueError("Death keyword absence requires sealed characteristic facts")
        if "creature" not in types or self.subtype not in subtypes:
            raise ValueError("Death keyword absence requires the original creature subtype")
        return self.keyword not in keywords


def is_death_keyword_absence(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("kind") == DEATH_KEYWORD_ABSENCE_KIND
