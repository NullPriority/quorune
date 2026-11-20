from __future__ import annotations

"""Closed compiler grammar for one permanent exploring once.

The templates in this module intentionally exclude repeated, simultaneous,
and replacement-style Explore wording.  Runtime execution owns CR 701.44;
the compiler only proves the small Oracle sentence shapes represented here.
"""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping


EXPLORE_MECHANIC_ID = "explore"


@dataclass(frozen=True, slots=True)
class ExploreEffectTemplate:
    template_id: str
    effects: tuple[Mapping[str, Any], ...]
    target_schema: Mapping[str, Any] | None
    mechanics: tuple[str, ...]

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


def single_explore_effect_template(
    text: str,
    *,
    allow_source_pronoun: bool = False,
) -> ExploreEffectTemplate | None:
    """Lower only an unmodified, single Explore instruction.

    ``it explores`` is accepted only when the caller has already bound the
    pronoun to the source permanent through a closed trigger grammar.
    """

    normalized = " ".join(text.strip().split())
    if re.fullmatch(
        r"this (?:artifact|creature|enchantment|land|permanent) explores\.?",
        normalized,
        re.IGNORECASE,
    ) or (
        allow_source_pronoun
        and re.fullmatch(r"it explores\.?", normalized, re.IGNORECASE)
    ):
        return ExploreEffectTemplate(
            template_id="explore-source-permanent-once-v1",
            effects=(
                {
                    "op": EXPLORE_MECHANIC_ID,
                    "player": "$source.controller",
                    "card": "$source",
                },
            ),
            target_schema=None,
            mechanics=(EXPLORE_MECHANIC_ID,),
        )
    if re.fullmatch(
        r"target creature you control explores\.?",
        normalized,
        re.IGNORECASE,
    ):
        return ExploreEffectTemplate(
            template_id="explore-target-controlled-creature-once-v1",
            effects=(
                {
                    "op": EXPLORE_MECHANIC_ID,
                    "player": "$target.controller.0",
                    "card": "$target.0",
                },
            ),
            target_schema={
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "types_any": ["creature"],
                "controller_relation": "you",
                "count": 1,
            },
            mechanics=(
                EXPLORE_MECHANIC_ID,
                "cr-115-targets",
            ),
        )
    return None


__all__ = [
    "EXPLORE_MECHANIC_ID",
    "ExploreEffectTemplate",
    "single_explore_effect_template",
]
