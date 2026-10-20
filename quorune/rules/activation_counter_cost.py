from __future__ import annotations

"""Typed fixed named-counter removal costs for activated abilities."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..counter_names import CounterStateError, normalized_counter_name


SOURCE_COUNTER_REMOVAL_COST_SCHEMA_VERSION = 1


class SourceCounterRemovalCostError(ValueError):
    """A source-local counter-removal cost is malformed."""


@dataclass(frozen=True, slots=True)
class SourceCounterRemovalCost:
    counter_name: str | None
    amount: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise SourceCounterRemovalCostError('Unsupported source counter-removal cost schema version')
        if self.schema_version == 2:
            if self.counter_name is not None or type(self.amount) is not int or self.amount not in {1, 2}:
                raise SourceCounterRemovalCostError('Any-kind source costs require one or two unspecified counters')
            return
        try:
            name = normalized_counter_name(self.counter_name)
        except CounterStateError as exc:
            raise SourceCounterRemovalCostError(str(exc)) from exc
        object.__setattr__(self, "counter_name", name)
        if type(self.amount) is not int or self.amount <= 0:
            raise SourceCounterRemovalCostError(
                "Source counter-removal cost amount must be positive"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "counter_name": self.counter_name,
            "amount": self.amount,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "SourceCounterRemovalCost":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "counter_name",
            "amount",
        }:
            raise SourceCounterRemovalCostError(
                "Source counter-removal costs use a closed schema"
            )
        if type(value['schema_version']) is not int or value["schema_version"] not in {1, 2}:
            raise SourceCounterRemovalCostError(
                "Unsupported source counter-removal cost schema version"
            )
        return cls(
            counter_name=value["counter_name"],
            amount=value["amount"],
            schema_version=value['schema_version'],
        )


__all__ = [
    "SOURCE_COUNTER_REMOVAL_COST_SCHEMA_VERSION",
    "SourceCounterRemovalCost",
    "SourceCounterRemovalCostError",
]
