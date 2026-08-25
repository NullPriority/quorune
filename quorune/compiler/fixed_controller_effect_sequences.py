from __future__ import annotations

"""Closed ordered controller draw/life/Scry/discard effect sequences."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .affected_player_discard_templates import (
    fixed_controller_discard_effect_template,
)
from .draw_templates import fixed_draw_effect_template
from .life_templates import fixed_life_effect_template
from .scry_templates import fixed_scry_effect_template


FIXED_CONTROLLER_SEQUENCE_MECHANIC = "fixed-controller-effect-sequence"


def _clauses(text: str) -> tuple[str, str] | None:
    normalized = " ".join(text.strip().split()).rstrip(".")
    if any(value in normalized for value in ('"', "(", ")")):
        return None
    for separator in (r"\.\s+", r",\s+then\s+", r"\s+and\s+"):
        parts = tuple(
            value.strip() for value in re.split(separator, normalized, maxsplit=1)
        )
        if len(parts) == 2 and all(parts):
            return parts
    return None


def fixed_controller_effect_clause(
    text: str,
) -> tuple[Mapping[str, Any], tuple[str, ...]] | None:
    draw = fixed_draw_effect_template(text)
    if draw is not None:
        _template, effects, target_schema, mechanics = draw
        if (
            target_schema is None
            and len(effects) == 1
            and set(effects[0]) == {"op", "player", "count", "private"}
            and effects[0].get("op") == "draw"
            and effects[0].get("player") == "$controller"
            and effects[0].get("private") is True
        ):
            return effects[0], mechanics
        return None
    discard = fixed_controller_discard_effect_template(text)
    if discard is not None:
        _template, effects, target_schema, mechanics = discard.compiled()
        if target_schema is None and len(effects) == 1:
            return effects[0], mechanics
        return None
    life = fixed_life_effect_template(text)
    if life is not None:
        _template, effects, target_schema, mechanics = life.compiled()
        if (
            target_schema is None
            and len(effects) == 1
            and effects[0].get("player") == "$controller"
        ):
            return effects[0], mechanics
        return None
    scry = fixed_scry_effect_template(text)
    if scry is not None:
        _template, effects, target_schema, mechanics = scry.compiled()
        if target_schema is None and len(effects) == 1:
            return effects[0], mechanics
    return None


@dataclass(frozen=True, slots=True)
class FixedControllerEffectSequenceTemplate:
    effects: tuple[Mapping[str, Any], ...]
    mechanic_ids: tuple[str, ...]

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return (
            "fixed-controller-draw-effect-sequence-v1",
            self.effects,
            None,
            self.mechanic_ids,
        )


def fixed_controller_effect_sequence_template(
    text: str,
) -> FixedControllerEffectSequenceTemplate | None:
    """Lower exactly two ordered fixed effects, including one card draw."""

    clauses = _clauses(text)
    if clauses is None:
        return None
    lowered = tuple(fixed_controller_effect_clause(clause) for clause in clauses)
    if any(value is None for value in lowered):
        return None
    compiled = tuple(value for value in lowered if value is not None)
    effects = tuple(value[0] for value in compiled)
    if sum(effect.get("op") == "draw" for effect in effects) != 1:
        return None
    return FixedControllerEffectSequenceTemplate(
        effects=effects,
        mechanic_ids=tuple(
            dict.fromkeys(
                (
                    FIXED_CONTROLLER_SEQUENCE_MECHANIC,
                    *(mechanic for value in compiled for mechanic in value[1]),
                )
            )
        ),
    )


__all__ = [
    "FIXED_CONTROLLER_SEQUENCE_MECHANIC",
    "FixedControllerEffectSequenceTemplate",
    "fixed_controller_effect_clause",
    "fixed_controller_effect_sequence_template",
]
