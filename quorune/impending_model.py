"""Typed ordinary-mana Impending cost and entry count."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re

from .fixed_mana_model import FIXED_MANA_COST_PATTERN, fixed_mana_variants


IMPENDING_MECHANIC = "impending"
IMPENDING_CAPABILITY = "casting.impending.fixed_mana"
IMPENDING_HANDLER_ID = "casting.impending.fixed-mana.v1"
IMPENDING_COST_EVENT = "cast.cost"
IMPENDING_CAST_OPTION = "impending"
IMPENDING_PRICE_FIELD = "impending_price"
PAID_IMPENDING_FIELD = "paid_impending"
_IMPENDING = re.compile(
    rf"Impending (?P<count>[1-9][0-9]*)[—–-](?P<cost>{FIXED_MANA_COST_PATTERN})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedManaImpendingSpec:
    time_counters: int
    mana_cost: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported Impending descriptor revision")
        if type(self.time_counters) is not int or self.time_counters <= 0:
            raise ValueError("Impending requires a positive printed time counter count")
        if (
            type(self.mana_cost) is not str
            or "/" in self.mana_cost
            or re.fullmatch(FIXED_MANA_COST_PATTERN, self.mana_cost) is None
        ):
            raise ValueError("Impending requires a fixed ordinary mana cost")

    @property
    def mana_requirements(self):
        return fixed_mana_variants(self.mana_cost)[0]

    def cast_cost_option(self) -> dict[str, object]:
        return {
            "id": IMPENDING_CAST_OPTION,
            "kind": "alternate",
            "label": f"Impending {self.time_counters}—{self.mana_cost}",
            "requirements": dict(self.mana_requirements),
            IMPENDING_PRICE_FIELD: self.to_dict(),
        }

    def to_dict(self) -> dict[str, int | str]:
        return {
            "time_counters": self.time_counters,
            "mana_cost": self.mana_cost,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> FixedManaImpendingSpec:
        if not isinstance(value, Mapping) or set(value) != {"time_counters", "mana_cost", "schema_version"}:
            raise ValueError("Unexpected Impending descriptor fields")
        return cls(value["time_counters"], value["mana_cost"], value["schema_version"])


def parse_fixed_mana_impending(material_line: str) -> FixedManaImpendingSpec | None:
    match = _IMPENDING.fullmatch(material_line)
    if match is None or "/" in match["cost"]:
        return None
    return FixedManaImpendingSpec(int(match["count"]), match["cost"].upper())


def impending_payment_context(selected_option: Mapping[str, object]) -> dict[str, object]:
    """Seal only the authoritative selected Impending price on the stack."""
    if IMPENDING_PRICE_FIELD not in selected_option:
        return {}
    if selected_option.get("id") != IMPENDING_CAST_OPTION or selected_option.get("kind") != "alternate":
        raise ValueError("Impending payment must come from its selected alternative")
    spec = FixedManaImpendingSpec.from_dict(selected_option[IMPENDING_PRICE_FIELD])
    return {PAID_IMPENDING_FIELD: spec.to_dict()}
