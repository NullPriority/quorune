"""A sealed optional hand payment whose generic reduction is fixed per card."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from ..additional_cost_vocabulary import EXILE_ONE_FROM_HAND_COST
from ..object_predicate import ObjectQuerySpec
from .casting_additional_costs import FixedZoneChangeAdditionalCost


COLORED_HAND_CREDIT = "optional_colored_hand_exile_credit"
COLORED_HAND_CREDIT_CAPABILITY = "casting.additional_cost.optional_colored_hand_exile_credit"
COLORED_HAND_CREDIT_DEPENDENCIES = (
    COLORED_HAND_CREDIT_CAPABILITY,
    "casting.additional_cost.zone_change.fixed_exile",
)


@dataclass(frozen=True, slots=True)
class ColoredHandCredit:
    color: str
    credit_per_card: ClassVar[int] = 2

    def __post_init__(self) -> None:
        if type(self.color) is not str or self.color not in {"W", "U", "B", "R", "G"}:
            raise ValueError("The hand-exile price requires one fixed printed color")

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "color", "credit_per_card"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["credit_per_card"]) is not int or value["credit_per_card"] != cls.credit_per_card):
            raise ValueError("The colored hand-exile price has one exact sealed shape")
        return cls(value["color"])

    def to_dict(self):
        return {"schema_version": 1, "color": self.color, "credit_per_card": self.credit_per_card}

    @property
    def leaf(self):
        return FixedZoneChangeAdditionalCost(
            EXILE_ONE_FROM_HAND_COST, "exile_cards",
            ObjectQuerySpec(zones=("hand",), owner="$actor", known_to_actor=True, colors_all=(self.color,)),
        )


def colored_hand_credit_from_cost_schema(value):
    if not isinstance(value, Mapping) or COLORED_HAND_CREDIT not in value:
        return None
    if set(value) != {COLORED_HAND_CREDIT}:
        raise ValueError("Colored hand-exile credit requires its complete isolated price")
    return ColoredHandCredit.from_dict(value[COLORED_HAND_CREDIT])


def colored_hand_credit_dependencies(cost_schema):
    return COLORED_HAND_CREDIT_DEPENDENCIES if colored_hand_credit_from_cost_schema(cost_schema) else ()
