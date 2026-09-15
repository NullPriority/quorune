from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping, Sequence

from .characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)


class EntryCounterError(ValueError):
    """An as-enters counter instruction is not representable."""


DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID = (
    "replacement.zone.dynamic-self-entry-counter.v1"
)
DYNAMIC_SELF_ENTRY_AMOUNTS_CONTEXT = "dynamic_self_entry_counter_amounts"


class DynamicEntryCounterValueSource(str, Enum):
    CAST_X = "cast_x"
    MANA_COLORS_SPENT = "mana_colors_spent"
    CONTROLLER_ATTACKED = "controller_attacked"
    CONTROLLER_OTHER_SPELLS_CAST = "controller_other_spells_cast"
    CONTROLLER_SPELLS_CAST = "controller_spells_cast"
    CREATURES_DIED = "creatures_died"
    OTHER_SPELLS_CAST = "other_spells_cast"
    OPPONENTS_LIFE_LOST = "opponents_life_lost"
    PUBLIC_QUERY = "public_query"
    CAST_FROM_HAND = "cast_from_hand"
    MANA_WAS_SPENT = "mana_was_spent"


class DynamicEntryCounterCalculation(str, Enum):
    MULTIPLY = "multiply"
    FIXED_IF_AT_LEAST = "fixed_if_at_least"
    FIXED_IF_BELOW = "fixed_if_below"


@dataclass(frozen=True, slots=True)
class DynamicEntryCounterAmountSpec:
    """One closed pre-entry amount calculation over public frozen facts."""

    value_source: DynamicEntryCounterValueSource
    calculation: DynamicEntryCounterCalculation
    coefficient: int = 1
    offset: int = 0
    minimum: int | None = None
    quantity: CharacteristicQuantitySpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.value_source, DynamicEntryCounterValueSource):
            try:
                object.__setattr__(
                    self,
                    "value_source",
                    DynamicEntryCounterValueSource(self.value_source),
                )
            except (TypeError, ValueError) as exc:
                raise EntryCounterError(
                    "Dynamic entry counter value source is unsupported"
                ) from exc
        if not isinstance(self.calculation, DynamicEntryCounterCalculation):
            try:
                object.__setattr__(
                    self,
                    "calculation",
                    DynamicEntryCounterCalculation(self.calculation),
                )
            except (TypeError, ValueError) as exc:
                raise EntryCounterError(
                    "Dynamic entry counter calculation is unsupported"
                ) from exc
        if type(self.coefficient) is not int or not 1 <= self.coefficient <= 10:
            raise EntryCounterError(
                "Dynamic entry counter coefficient must be from 1 through 10"
            )
        if type(self.offset) is not int or not 0 <= self.offset <= 10:
            raise EntryCounterError(
                "Dynamic entry counter offset must be from 0 through 10"
            )
        if self.calculation is DynamicEntryCounterCalculation.MULTIPLY:
            if self.minimum is not None:
                raise EntryCounterError(
                    "Multiplying entry counter amounts take no minimum"
                )
        elif type(self.minimum) is not int or self.minimum < 1:
            raise EntryCounterError(
                "Conditional entry counter amounts require a positive minimum"
            )
        elif self.offset:
            raise EntryCounterError(
                "Conditional entry counter amounts cannot use an offset"
            )
        if self.value_source is DynamicEntryCounterValueSource.PUBLIC_QUERY:
            quantity = self.quantity
            if (
                not isinstance(quantity, CharacteristicQuantitySpec)
                or quantity.scope
                not in {
                    CharacteristicQuantityScope.CONTROLLER_ZONE,
                    CharacteristicQuantityScope.OPPONENT_ZONES,
                    CharacteristicQuantityScope.ALL_ZONES,
                }
                or quantity.query is None
                or quantity.query.zones
                not in {("battlefield",), ("graveyard",)}
            ):
                raise EntryCounterError(
                    "Dynamic entry counter queries require the cycle-safe "
                    "public layer-5 boundary"
                )
        elif self.quantity is not None:
            raise EntryCounterError(
                "Only public-query entry counter amounts accept a quantity"
            )

    def amount(self, value: int) -> int:
        if type(value) is not int or value < 0:
            raise EntryCounterError(
                "Dynamic entry counter facts must be nonnegative integers"
            )
        if self.calculation is DynamicEntryCounterCalculation.MULTIPLY:
            return self.offset + self.coefficient * value
        assert self.minimum is not None
        admitted = (
            value >= self.minimum
            if self.calculation
            is DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST
            else value < self.minimum
        )
        return self.coefficient if admitted else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "value_source": self.value_source.value,
            "calculation": self.calculation.value,
            "coefficient": self.coefficient,
            "offset": self.offset,
            "minimum": self.minimum,
            "quantity": (
                self.quantity.to_dict() if self.quantity is not None else None
            ),
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "DynamicEntryCounterAmountSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "value_source",
            "calculation",
            "coefficient",
            "offset",
            "minimum",
            "quantity",
        }:
            raise EntryCounterError(
                "Dynamic entry counter amount uses a closed schema"
            )
        try:
            quantity = (
                CharacteristicQuantitySpec.from_dict(value["quantity"])
                if value["quantity"] is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise EntryCounterError(str(exc)) from exc
        return cls(
            value_source=value["value_source"],
            calculation=value["calculation"],
            coefficient=value["coefficient"],
            offset=value["offset"],
            minimum=value["minimum"],
            quantity=quantity,
        )


def _normalized_nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str:
        raise EntryCounterError(f"{field} must be a string")
    normalized = " ".join(value.casefold().split())
    if not normalized:
        raise EntryCounterError(f"{field} must be nonempty")
    return normalized


@dataclass(frozen=True, slots=True)
class IntrinsicEntryCounter:
    counter_name: str
    amount: int
    required_type: str
    rule_id: str

    def __post_init__(self) -> None:
        counter_name = " ".join(self.counter_name.casefold().split())
        required_type = " ".join(self.required_type.casefold().split())
        rule_id = str(self.rule_id or "")
        if not counter_name or not required_type or not rule_id:
            raise EntryCounterError(
                "Intrinsic entry counters require a counter, type, and rule"
            )
        if type(self.amount) is not int or self.amount < 0:
            raise EntryCounterError(
                "Intrinsic entry counter amounts must be nonnegative integers"
            )
        object.__setattr__(self, "counter_name", counter_name)
        object.__setattr__(self, "required_type", required_type)
        object.__setattr__(self, "rule_id", rule_id)

    @property
    def capability_id(self) -> str:
        if self.required_type == "saga":
            return "counter.producer.saga_lore"
        return "counter.producer.intrinsic_entry"


@dataclass(frozen=True, slots=True)
class EffectEntryCounter:
    """One effect-generated counter attached to a proposed battlefield entry."""

    counter_name: str
    amount: int
    placing_player: str
    source_ref: str
    rule_id: str

    def __post_init__(self) -> None:
        counter_name = _normalized_nonempty(
            self.counter_name,
            field="Effect entry counter name",
        )
        if type(self.amount) is not int or self.amount < 1:
            raise EntryCounterError(
                "Effect entry counter amounts must be positive integers"
            )
        for field_name in ("placing_player", "source_ref", "rule_id"):
            value = getattr(self, field_name)
            if type(value) is not str or not value or value != value.strip():
                raise EntryCounterError(
                    f"Effect entry counter {field_name} must be a canonical "
                    "nonempty string"
                )
        object.__setattr__(self, "counter_name", counter_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "counter_name": self.counter_name,
            "amount": self.amount,
            "placing_player": self.placing_player,
            "source_ref": self.source_ref,
            "rule_id": self.rule_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EffectEntryCounter":
        if not isinstance(value, Mapping):
            raise EntryCounterError(
                "Effect entry counter serialization must be an object"
            )
        expected = {
            "counter_name",
            "amount",
            "placing_player",
            "source_ref",
            "rule_id",
        }
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        if missing or unknown:
            details = [
                *(f"missing {field}" for field in missing),
                *(f"unknown {field}" for field in unknown),
            ]
            raise EntryCounterError(
                "Effect entry counter fields: " + "; ".join(details)
            )
        return cls(
            counter_name=value["counter_name"],
            amount=value["amount"],
            placing_player=value["placing_player"],
            source_ref=value["source_ref"],
            rule_id=value["rule_id"],
        )


def _printed_nonnegative_integer(
    value: Any,
    *,
    characteristic: str,
) -> int:
    if type(value) is int:
        amount = value
    elif type(value) is str and re.fullmatch(r"-?\d+", value.strip()):
        amount = int(value.strip())
    else:
        raise EntryCounterError(
            f"{characteristic} must be a represented nonnegative integer"
        )
    if amount < 0:
        raise EntryCounterError(f"{characteristic} cannot be negative")
    return amount


def intrinsic_entry_counters(
    characteristics: Mapping[str, Any],
    *,
    card_types: Sequence[str],
    card_subtypes: Sequence[str] = (),
    keywords: Sequence[str] = (),
    read_ahead_supported: bool = False,
) -> tuple[IntrinsicEntryCounter, ...]:
    """Return closed rules-derived battlefield entry-counter instructions."""

    if not isinstance(characteristics, Mapping):
        raise EntryCounterError(
            "Entry counter characteristics must be a mapping"
        )
    types = {" ".join(str(value).casefold().split()) for value in card_types}
    subtypes = {
        " ".join(str(value).casefold().split()) for value in card_subtypes
    }
    normalized_keywords = {
        " ".join(str(value).casefold().split()) for value in keywords
    }
    counters: list[IntrinsicEntryCounter] = []
    if "planeswalker" in types:
        counters.append(
            IntrinsicEntryCounter(
                counter_name="loyalty",
                amount=_printed_nonnegative_integer(
                    characteristics.get("loyalty"),
                    characteristic="Starting loyalty",
                ),
                required_type="planeswalker",
                rule_id="306.5b",
            )
        )
    if "battle" in types:
        counters.append(
            IntrinsicEntryCounter(
                counter_name="defense",
                amount=_printed_nonnegative_integer(
                    characteristics.get("defense"),
                    characteristic="Battle defense",
                ),
                required_type="battle",
                rule_id="310.4b",
            )
        )
    if "saga" in subtypes:
        if "read ahead" in normalized_keywords:
            if read_ahead_supported:
                return tuple(counters)
            raise EntryCounterError(
                "Read Ahead Saga entry requires its unrepresented chapter "
                "number choice"
            )
        counters.append(
            IntrinsicEntryCounter(
                counter_name="lore",
                amount=1,
                required_type="saga",
                rule_id="714.3a",
            )
        )
    return tuple(counters)


__all__ = [
    "DYNAMIC_SELF_ENTRY_AMOUNTS_CONTEXT",
    "DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID",
    "DynamicEntryCounterAmountSpec",
    "DynamicEntryCounterCalculation",
    "DynamicEntryCounterValueSource",
    "EntryCounterError",
    "EffectEntryCounter",
    "IntrinsicEntryCounter",
    "intrinsic_entry_counters",
]
