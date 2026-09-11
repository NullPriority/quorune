from __future__ import annotations

"""Composition boundary for source-self trigger effect bodies."""

from typing import Any

from .creature_power_damage_templates import (
    fixed_creature_power_damage_effect_template,
)
from .damage_templates import source_pronoun_damage_effect_template
from .explore_templates import single_explore_effect_template


def source_self_contextual_effect_template(
    text: str,
    *,
    card_name: str,
    event_phrase: str,
) -> tuple[Any, ...] | None:
    """Delegate one source-bound body to its closed typed leaf owner."""

    explored = single_explore_effect_template(
        text,
        allow_source_pronoun=True,
    )
    creature_power = fixed_creature_power_damage_effect_template(
        text,
        card_name=card_name,
        allow_source_pronoun=True,
    )
    fixed_damage = (
        source_pronoun_damage_effect_template(text)
        if event_phrase in {"enters", "dies"}
        else None
    )
    compiled = explored or creature_power or fixed_damage
    return compiled.compiled() if compiled is not None else None


__all__ = ["source_self_contextual_effect_template"]
