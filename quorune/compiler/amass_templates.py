from __future__ import annotations

"""Closed Oracle lowering for one fixed positive Amass instruction."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping

from ..amass import FixedAmassSpec, canonical_amass_subtype
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


@dataclass(frozen=True, slots=True)
class FixedAmassTemplate:
    spec: FixedAmassSpec

    @property
    def template_id(self) -> str:
        subtype = self.spec.subtype.casefold().replace(" ", "-")
        return f"amass-{subtype}-fixed-{self.spec.amount}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "amass",
                "subtype": self.spec.subtype,
                "amount": self.spec.amount,
            },
        )

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("amass", "cr-111-tokens", "cr-122-counters")

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return self.template_id, self.effects, None, self.mechanics


def fixed_amass_effect_template(text: str) -> FixedAmassTemplate | None:
    """Parse exactly one unmodified fixed positive Amass action."""

    match = re.fullmatch(
        rf"amass (?P<subtype>.+?) "
        rf"(?P<amount>{FIXED_COUNT_PATTERN})\.?",
        " ".join(text.strip().split()),
        re.IGNORECASE,
    )
    if match is None:
        return None
    subtype = canonical_amass_subtype(match.group("subtype"))
    amount = fixed_number(match.group("amount"))
    if subtype is None or amount <= 0:
        return None
    return FixedAmassTemplate(FixedAmassSpec(subtype=subtype, amount=amount))


__all__ = ["FixedAmassTemplate", "fixed_amass_effect_template"]
