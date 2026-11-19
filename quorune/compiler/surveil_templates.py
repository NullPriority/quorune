from __future__ import annotations

"""Closed Oracle lowering for fixed controller Surveil instructions."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


SURVEIL_MECHANIC_ID = "surveil"
SURVEIL_CAPABILITY_ID = "library.surveil.fixed_controller"


@dataclass(frozen=True, slots=True)
class FixedSurveilEffectTemplate:
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
            "surveil-fixed-controller-v1",
            (
                {
                    "op": "surveil",
                    "player": "$controller",
                    "count": self.count,
                },
            ),
            None,
            (SURVEIL_MECHANIC_ID,),
        )


def fixed_surveil_effect_template(
    text: str,
) -> FixedSurveilEffectTemplate | None:
    """Lower one mandatory positive fixed-count controller Surveil."""

    match = re.fullmatch(
        rf"surveil (?P<count>{FIXED_COUNT_PATTERN})\.?",
        " ".join(text.strip().split()),
        re.IGNORECASE,
    )
    if match is None:
        return None
    count = fixed_number(match.group("count"))
    return FixedSurveilEffectTemplate(count=count) if count > 0 else None


__all__ = [
    "fixed_surveil_effect_template",
    "FixedSurveilEffectTemplate",
    "SURVEIL_CAPABILITY_ID",
    "SURVEIL_MECHANIC_ID",
]
