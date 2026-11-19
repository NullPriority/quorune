from __future__ import annotations

"""Closed compiler grammar for one unmodified Proliferate instruction."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ProliferateEffectTemplate:
    template_id: str = "proliferate-once-v1"
    effects: tuple[Mapping[str, Any], ...] = ({"op": "proliferate"},)
    target_schema: Mapping[str, Any] | None = None
    mechanics: tuple[str, ...] = ("proliferate",)

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def single_proliferate_effect_template(
    text: str,
) -> ProliferateEffectTemplate | None:
    """Lower exactly one ordinary CR 701.34a instruction."""

    normalized = " ".join(text.strip().split())
    if not re.fullmatch(r"proliferate\.?", normalized, re.IGNORECASE):
        return None
    return ProliferateEffectTemplate()


__all__ = [
    "ProliferateEffectTemplate",
    "single_proliferate_effect_template",
]
