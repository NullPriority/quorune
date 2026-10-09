from __future__ import annotations

"""Composition boundary for source-self trigger effect bodies."""

from typing import Any
import re
from ..rules.source_references import SourceReferenceSpec

from .creature_power_damage_templates import (
    fixed_creature_power_damage_effect_template,
)
from .damage_templates import source_pronoun_damage_effect_template
from .explore_templates import single_explore_effect_template
from .declared_effect_amounts import declared_effect_amount_template


_SOURCE_EVENT = re.compile(
    r"(?P<prefix>(?:When|Whenever) )(?P<subject>.+?)"
    r"(?P<tail> (?:attacks|blocks|becomes blocked|deals combat damage to a player|"
    r"deals combat damage to an opponent|deals damage to an opponent|is dealt damage), .+)",
    re.IGNORECASE,
)
_NAMED_SOURCE_UNION_PREFIX = re.compile(
    r"^Whenever (?s:.*?) or another [A-Za-z][A-Za-z'-]*\b", re.IGNORECASE
)


def named_source_union_candidate(text: str) -> bool:
    """Retain a superset of the existing named-source union prefix."""
    return _NAMED_SOURCE_UNION_PREFIX.match(text) is not None


def normalized_source_event_line(text: str, *, source_name: str) -> str | None:
    """Normalize only an independently closed self-action event subject."""
    if _SOURCE_EVENT.fullmatch(text) is None:
        return None
    source = re.fullmatch(
        rf"(?P<prefix>(?:When|Whenever) )(?P<subject>{SourceReferenceSpec(source_name).regex_pattern}"
        r"|this (?:artifact|enchantment|land|permanent|planeswalker|Saga|Spacecraft|Class|Vehicle))"
        r"(?P<tail> (?:attacks|blocks|becomes blocked|deals combat damage to a player|"
        r"deals combat damage to an opponent|deals damage to an opponent|is dealt damage), .+)",
        text, re.IGNORECASE,
    )
    # The label identifies this object under CR 201.5, not a type predicate.
    return source["prefix"] + "this creature" + source["tail"] if source is not None else None


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
    if compiled is not None:
        return compiled.compiled()
    if event_phrase not in {"enters", "dies"}:
        return None
    def compile_fixed(body: str):
        leaf = source_pronoun_damage_effect_template(body)
        return leaf.compiled() if leaf is not None else (None, (), None, ())
    return declared_effect_amount_template(
        text, source_name=card_name, compile_fixed=compile_fixed,
    )


__all__ = ["source_self_contextual_effect_template"]
