from __future__ import annotations

"""Closed source-threaded sequences of fixed resolution effects."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .counter_placement_templates import (
    fixed_counter_placement_effect_template,
)
from .fixed_target_effect_sequences import (
    fixed_target_characteristics_effect_template,
)


SOURCE_ZONE_OBJECT = "$source.zone_object"
FIXED_SOURCE_SEQUENCE_MECHANIC = "fixed-source-effect-sequence"


def _sentences(text: str) -> tuple[str, str] | None:
    normalized = " ".join(text.strip().split())
    if any(value in normalized for value in ('"', "(", ")")):
        return None
    clauses = tuple(
        value.strip() + "."
        for value in re.split(r"\.\s+", normalized.rstrip("."))
        if value.strip()
    )
    return clauses if len(clauses) == 2 else None


@dataclass(frozen=True, slots=True)
class FixedSourceEffectSequenceTemplate:
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
            "fixed-source-counter-characteristics-sequence-v1",
            self.effects,
            None,
            self.mechanic_ids,
        )


def fixed_source_effect_sequence_template(
    text: str,
    *,
    card_name: str,
    source_is_permanent: bool | None,
) -> FixedSourceEffectSequenceTemplate | None:
    """Lower a fixed source counter followed by one temporary characteristic."""

    if source_is_permanent is not True:
        return None
    clauses = _sentences(text)
    if clauses is None:
        return None
    counter = fixed_counter_placement_effect_template(
        clauses[0],
        card_name=card_name,
    )
    characteristic = fixed_target_characteristics_effect_template(
        clauses[1],
        existing_target=True,
    )
    if counter is None or characteristic is None:
        return None
    (
        _counter_template,
        counter_effects,
        counter_target,
        counter_mechanics,
    ) = counter.compiled()
    (
        _characteristic_template,
        characteristic_effects,
        characteristic_target,
        characteristic_mechanics,
    ) = characteristic.compiled()
    if (
        counter_target is not None
        or characteristic_target is not None
        or len(counter_effects) != 1
        or counter_effects[0].get("card") != SOURCE_ZONE_OBJECT
        or not characteristic_effects
        or any(
            effect.get("card") != "$target.0"
            for effect in characteristic_effects
        )
    ):
        return None
    effects = (
        counter_effects[0],
        *(
            {**effect, "card": SOURCE_ZONE_OBJECT}
            for effect in characteristic_effects
        ),
    )
    return FixedSourceEffectSequenceTemplate(
        effects=effects,
        mechanic_ids=tuple(
            dict.fromkeys(
                (
                    FIXED_SOURCE_SEQUENCE_MECHANIC,
                    "cr-122-counters",
                    "cr-611-continuous-effects",
                    *counter_mechanics,
                    *characteristic_mechanics,
                )
            )
        ),
    )


__all__ = [
    "FIXED_SOURCE_SEQUENCE_MECHANIC",
    "SOURCE_ZONE_OBJECT",
    "FixedSourceEffectSequenceTemplate",
    "fixed_source_effect_sequence_template",
]
