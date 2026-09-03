from __future__ import annotations

"""Closed fixed-mana casting lifecycles with typed post-cast destinations."""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping

from ..util import mana_cost_to_vector


class FixedCastLifecycleKind(str, Enum):
    BUYBACK = "buyback"
    DASH = "dash"
    WARP = "warp"


_MANA_FIELDS = ("GENERIC", "W", "U", "B", "R", "G", "C")
_ORDINARY_COST = r"(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+"
_FIXED_LIFECYCLE = re.compile(
    rf"^(?P<mechanic>Buyback|Dash|Warp) (?P<cost>{_ORDINARY_COST})"
    r"(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedCastLifecycleSpec:
    kind: FixedCastLifecycleKind
    cost_text: str
    mana_cost: Mapping[str, int]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported fixed cast-lifecycle schema version")
        if not isinstance(self.kind, FixedCastLifecycleKind):
            raise ValueError("Fixed cast lifecycle kind is unsupported")
        if not isinstance(self.cost_text, str) or re.fullmatch(
            _ORDINARY_COST, self.cost_text
        ) is None:
            raise ValueError("Fixed cast lifecycle requires ordinary mana")
        expected, complex_symbols = mana_cost_to_vector(self.cost_text)
        if (
            not isinstance(self.mana_cost, Mapping)
            or set(self.mana_cost) != set(_MANA_FIELDS)
            or complex_symbols
            or dict(self.mana_cost) != expected
        ):
            raise ValueError("Fixed cast lifecycle mana vector is malformed")

    @property
    def cost_kind(self) -> str:
        return "additional" if self.kind is FixedCastLifecycleKind.BUYBACK else "alternate"

    @property
    def resolution_destination(self) -> str | None:
        return "hand" if self.kind is FixedCastLifecycleKind.BUYBACK else None

    @property
    def delayed_battlefield_destination(self) -> str | None:
        if self.kind is FixedCastLifecycleKind.DASH:
            return "hand"
        if self.kind is FixedCastLifecycleKind.WARP:
            return "exile"
        return None

    @property
    def grants_haste(self) -> bool:
        return self.kind is FixedCastLifecycleKind.DASH

    @property
    def grants_exile_recast(self) -> bool:
        return self.kind is FixedCastLifecycleKind.WARP

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "cost_text": self.cost_text,
            "mana_cost": dict(self.mana_cost),
        }


def fixed_cast_lifecycle_spec(text: str) -> FixedCastLifecycleSpec | None:
    """Parse only ordinary fixed-mana Buyback, Dash, or Warp."""

    match = _FIXED_LIFECYCLE.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    cost_text = match.group("cost").upper()
    mana_cost, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols:
        return None
    return FixedCastLifecycleSpec(
        kind=FixedCastLifecycleKind(match.group("mechanic").casefold()),
        cost_text=cost_text,
        mana_cost=mana_cost,
    )


__all__ = [
    "FixedCastLifecycleKind",
    "FixedCastLifecycleSpec",
    "fixed_cast_lifecycle_spec",
]
