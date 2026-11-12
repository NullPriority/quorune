from __future__ import annotations

"""Closed Oracle lowering for fixed controller Scry instructions."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


SCRY_MECHANIC = "scry"


@dataclass(frozen=True, slots=True)
class FixedScryEffectTemplate:
    count: int

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return (
            "scry-controller-v1",
            (
                {
                    "op": "scry",
                    "player": "$controller",
                    "count": self.count,
                },
            ),
            None,
            (SCRY_MECHANIC,),
        )


def fixed_scry_effect_template(
    text: str,
) -> FixedScryEffectTemplate | None:
    """Lower one positive fixed controller Scry instruction."""

    match = re.fullmatch(
        rf"(?:you )?scry (?P<count>{FIXED_COUNT_PATTERN})\.?",
        " ".join(text.strip().split()),
        re.IGNORECASE,
    )
    if match is None:
        return None
    count = fixed_number(match.group("count"))
    return FixedScryEffectTemplate(count=count) if count > 0 else None


__all__ = [
    "SCRY_MECHANIC",
    "FixedScryEffectTemplate",
    "fixed_scry_effect_template",
]
