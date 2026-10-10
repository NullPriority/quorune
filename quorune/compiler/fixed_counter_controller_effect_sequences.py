from __future__ import annotations

"""Closed ordered sequences containing one counter and one controller effect."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .counter_placement_templates import (
    fixed_counter_placement_effect_template,
)
from .fixed_controller_effect_sequences import (
    fixed_controller_effect_clause,
)


FIXED_COUNTER_CONTROLLER_SEQUENCE_MECHANIC = (
    "fixed-counter-controller-effect-sequence"
)


def _clauses(text: str) -> tuple[str, str] | None:
    normalized = " ".join(text.strip().split()).rstrip(".")
    if any(value in normalized for value in ('"', "(", ")")):
        return None
    for separator in (r"\.\s+", r",\s+then\s+", r"\s+and\s+"):
        parts = tuple(
            value.strip()
            for value in re.split(
                separator,
                normalized,
                maxsplit=1,
                flags=re.IGNORECASE,
            )
        )
        if len(parts) == 2 and all(parts):
            return parts
    return None


@dataclass(frozen=True, slots=True)
class FixedCounterControllerEffectSequenceTemplate:
    effects: tuple[Mapping[str, Any], ...]
    target_schema: Mapping[str, Any] | None
    mechanic_ids: tuple[str, ...]

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            "fixed-counter-controller-effect-sequence-v1",
            self.effects,
            self.target_schema,
            self.mechanic_ids,
        )


def fixed_counter_controller_effect_sequence_template(
    text: str,
    *,
    card_name: str,
) -> FixedCounterControllerEffectSequenceTemplate | None:
    """Lower one fixed counter placement and one fixed controller effect."""

    clauses = _clauses(text)
    if clauses is None:
        return None
    counter_indexes = tuple(
        index
        for index, clause in enumerate(clauses)
        if fixed_counter_placement_effect_template(
            clause,
            card_name=card_name,
        )
        is not None
    )
    if len(counter_indexes) != 1:
        return None
    counter_index = counter_indexes[0]
    counter = fixed_counter_placement_effect_template(
        clauses[counter_index],
        card_name=card_name,
    )
    controller = fixed_controller_effect_clause(clauses[1 - counter_index])
    if counter is None or controller is None:
        return None
    (
        _counter_template,
        counter_effects,
        target_schema,
        counter_mechanics,
    ) = counter.compiled()
    if len(counter_effects) != 1:
        return None
    counter_effect = counter_effects[0]
    effects = (
        (counter_effect, controller[0])
        if counter_index == 0
        else (controller[0], counter_effect)
    )
    return FixedCounterControllerEffectSequenceTemplate(
        effects=effects,
        target_schema=target_schema,
        mechanic_ids=tuple(
            dict.fromkeys(
                (
                    FIXED_COUNTER_CONTROLLER_SEQUENCE_MECHANIC,
                    *counter_mechanics,
                    *controller[1],
                )
            )
        ),
    )


__all__ = [
    "FIXED_COUNTER_CONTROLLER_SEQUENCE_MECHANIC",
    "FixedCounterControllerEffectSequenceTemplate",
    "fixed_counter_controller_effect_sequence_template",
]
