from __future__ import annotations

"""Typed CR 122.1b keyword-counter characteristic projection."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


class KeywordCounterError(ValueError):
    """A represented keyword-counter quantity or entry choice is malformed."""


# CR 122.1b's closed ordinary keyword-counter vocabulary. Variants of these
# keywords require their own typed grammar and deliberately do not normalize
# through this table.
KEYWORD_COUNTER_MECHANICS: Mapping[str, str] = MappingProxyType(
    {
        "flying": "flying",
        "first strike": "first-strike",
        "double strike": "double-strike",
        "deathtouch": "deathtouch",
        "decayed": "decayed",
        "exalted": "exalted",
        "haste": "haste",
        "hexproof": "hexproof",
        "indestructible": "indestructible",
        "lifelink": "lifelink",
        "menace": "menace",
        "reach": "reach",
        "shadow": "shadow",
        "trample": "trample",
        "vigilance": "vigilance",
    }
)


def keyword_counter_mechanic(counter_name: object) -> str | None:
    """Return the exact mechanic dependency for one ordinary counter name."""

    if not isinstance(counter_name, str):
        return None
    normalized = " ".join(counter_name.casefold().split())
    return KEYWORD_COUNTER_MECHANICS.get(normalized)


FIXED_KEYWORD_COUNTER_CHOICE_CAPABILITY = "counter.producer.fixed_keyword_entry_choice"
FIXED_KEYWORD_COUNTER_CHOICE_HANDLER = "replacement.zone.keyword-counter-entry-choice.v1"


@dataclass(frozen=True, slots=True)
class KeywordCounterEntryChoiceSpec:
    """One of two distinct ordinary keyword counters as this object enters."""

    counter_names: tuple[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.counter_names, (tuple, list)) or len(self.counter_names) != 2:
            raise KeywordCounterError("Entry keyword choice requires exactly two counters")
        if any(type(name) is not str for name in self.counter_names):
            raise KeywordCounterError("Entry keyword choice names must be strings")
        names = tuple(" ".join(name.casefold().split()) for name in self.counter_names)
        if names[0] == names[1] or any(keyword_counter_mechanic(name) is None for name in names):
            raise KeywordCounterError("Entry keyword choice requires distinct ordinary keyword counters")
        object.__setattr__(self, "counter_names", names)

    def descriptor(self) -> dict[str, Any]:
        return {"handler_id": FIXED_KEYWORD_COUNTER_CHOICE_HANDLER, "schema_version": 1,
            "event": "zone.change", "counter_names": list(self.counter_names), "rule_id": "122.1b"}

    @property
    def mechanics(self) -> tuple[str, ...]:
        return tuple(KEYWORD_COUNTER_MECHANICS[name] for name in self.counter_names)

    @classmethod
    def from_descriptor(cls, value: Mapping[str, Any]) -> "KeywordCounterEntryChoiceSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "handler_id", "schema_version", "event", "counter_names", "rule_id"
        }:
            raise KeywordCounterError("Entry keyword choice uses a closed descriptor")
        if (value["handler_id"] != FIXED_KEYWORD_COUNTER_CHOICE_HANDLER
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["event"] != "zone.change" or value["rule_id"] != "122.1b"):
            raise KeywordCounterError("Entry keyword choice identity, event or schema changed")
        return cls(value["counter_names"])


def keyword_counter_abilities(counters: Mapping[str, Any]) -> tuple[str, ...]:
    """Return abilities granted by positive represented keyword counters."""

    if not isinstance(counters, Mapping):
        raise KeywordCounterError("Counters must be a mapping")
    abilities: list[str] = []
    for counter_name, mechanic_id in KEYWORD_COUNTER_MECHANICS.items():
        if counter_name not in counters:
            continue
        amount = counters[counter_name]
        if type(amount) is not int or amount < 0:
            raise KeywordCounterError(
                f"{counter_name} counter quantity must be a nonnegative integer"
            )
        if amount > 0:
            abilities.append(mechanic_id.replace("-", " ").title())
    return tuple(abilities)


__all__ = [
    "KEYWORD_COUNTER_MECHANICS",
    "KeywordCounterError",
    "keyword_counter_abilities",
    "keyword_counter_mechanic",
    "KeywordCounterEntryChoiceSpec",
    "FIXED_KEYWORD_COUNTER_CHOICE_CAPABILITY",
    "FIXED_KEYWORD_COUNTER_CHOICE_HANDLER",
]
