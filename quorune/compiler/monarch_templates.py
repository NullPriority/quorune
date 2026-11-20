from __future__ import annotations

"""Closed controller-designation grammar for the monarch variant."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping


MONARCH_MECHANIC = "cr-725-the-monarch"


@dataclass(frozen=True, slots=True)
class FixedMonarchTemplate:
    template_id: str = "become-monarch-controller-v1"

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
            ({"op": "become_monarch", "player": "$controller"},),
            None,
            (MONARCH_MECHANIC,),
        )


def fixed_monarch_effect_template(text: str) -> FixedMonarchTemplate | None:
    """Lower only the mandatory controller-becomes-monarch instruction."""

    if re.fullmatch(
        r"you become the monarch\.?",
        text.strip(),
        re.IGNORECASE,
    ) is None:
        return None
    return FixedMonarchTemplate()


__all__ = [
    "MONARCH_MECHANIC",
    "FixedMonarchTemplate",
    "fixed_monarch_effect_template",
]
