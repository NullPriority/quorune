from __future__ import annotations

"""Closed Oracle lowering for one ordinary fixed Bolster instruction."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


@dataclass(frozen=True, slots=True)
class FixedBolsterTemplate:
    """One fixed positive Bolster N keyword action."""

    amount: int

    def __post_init__(self) -> None:
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError("Bolster amount must be a positive exact integer")

    @property
    def template_id(self) -> str:
        return f"bolster-fixed-{self.amount}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "fixed_bolster",
                "player": "$controller",
                "amount": self.amount,
            },
        )

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("bolster", "cr-122-counters")

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return self.template_id, self.effects, None, self.mechanics


def fixed_bolster_effect_template(text: str) -> FixedBolsterTemplate | None:
    """Parse exactly one unmodified fixed positive Bolster N action."""

    match = re.fullmatch(
        rf"bolster (?P<amount>{FIXED_COUNT_PATTERN})\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    amount = fixed_number(match.group("amount"))
    return FixedBolsterTemplate(amount) if amount > 0 else None


__all__ = ["FixedBolsterTemplate", "fixed_bolster_effect_template"]
