from __future__ import annotations

"""Low-level canonical counter-name values without state-model dependencies."""

from typing import Any, Mapping

EXISTING_COUNTER_AMOUNT = "existing_named_counter_count"


def existing_counter_amount_descriptor() -> dict[str, Any]:
    return {"kind": EXISTING_COUNTER_AMOUNT, "schema_version": 1}


def is_existing_counter_amount(value: Any) -> bool:
    return (isinstance(value, Mapping) and set(value) == {"kind", "schema_version"}
            and value["kind"] == EXISTING_COUNTER_AMOUNT
            and type(value["schema_version"]) is int and value["schema_version"] == 1)


class CounterStateError(ValueError):
    """A typed counter change cannot be planned or committed exactly."""


def normalized_counter_name(value: str) -> str:
    result = " ".join(str(value).casefold().split())
    if not result:
        raise CounterStateError("Counter changes require a counter name")
    return result


__all__ = ["CounterStateError", "normalized_counter_name", "EXISTING_COUNTER_AMOUNT", "existing_counter_amount_descriptor", "is_existing_counter_amount"]
