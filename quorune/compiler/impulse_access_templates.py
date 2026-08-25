from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


IMPULSE_ACCESS_CAPABILITY_ID = "zone.impulse_access.fixed"
IMPULSE_ACCESS_MECHANIC_ID = "fixed-impulse-access"
_FIXED_IMPULSE_ACCESS = re.compile(
    rf"^Exile the top (?:(?P<single>card)|(?P<count>{FIXED_COUNT_PATTERN}) cards) "
    rf"of your library\. "
    rf"(?:(?P<prefix>Until (?:the )?end of (?:(?:this|your next) turn|turn)), "
    rf"you may play (?P<prefix_ref>that card|those cards|it|them)|"
    rf"You may play (?P<suffix_ref>that card|those cards|it|them) "
    rf"(?P<suffix>this turn|until (?:the )?end of your next turn))\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedImpulseAccessTemplate:
    count: int
    duration: str

    @property
    def template_id(self) -> str:
        suffix = "next-turn" if self.duration.endswith("next_turn") else "turn"
        return f"fixed-impulse-access-{suffix}-v1"

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            (
                {
                    "op": "fixed_impulse_access",
                    "player": "$controller",
                    "count": self.count,
                    "duration": self.duration,
                },
            ),
            None,
            (IMPULSE_ACCESS_MECHANIC_ID,),
        )


def fixed_impulse_access_effect_template(
    text: str,
) -> FixedImpulseAccessTemplate | None:
    """Lower only fixed own-library exile with unrestricted play access."""

    match = _FIXED_IMPULSE_ACCESS.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    count = 1 if match.group("single") else fixed_number(match.group("count"))
    reference = str(match.group("prefix_ref") or match.group("suffix_ref")).casefold()
    if (count == 1) != (reference in {"that card", "it"}):
        return None
    duration_text = str(match.group("prefix") or match.group("suffix")).casefold()
    duration = (
        "until_end_of_next_turn"
        if "next turn" in duration_text
        else "until_end_of_turn"
    )
    return FixedImpulseAccessTemplate(count=count, duration=duration)


__all__ = [
    "fixed_impulse_access_effect_template",
    "FixedImpulseAccessTemplate",
    "IMPULSE_ACCESS_CAPABILITY_ID",
    "IMPULSE_ACCESS_MECHANIC_ID",
]
