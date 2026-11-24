from __future__ import annotations

"""Closed attached ability lists and their existing typed fragments."""

from . import regex_working_set as re
from typing import Any, Mapping

from ..ability_fragments import ToxicSpec, ability_fragment_to_dict, parse_protection_line
from ..keyword_abilities import (
    FIXED_CHARACTERISTIC_KEYWORDS, FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES,
    triggered_keyword_grant,
)
from .ward_cost_templates import fixed_ward_spec

_ATTACHED_SUPPORTED_ABILITIES = frozenset(
    keyword.casefold() for keyword in FIXED_CHARACTERISTIC_KEYWORDS
) | {'flash'}


FIXED_KEYWORDS_AND_COLOR_PROTECTION_TEMPLATE = "continuous-fixed-query-keywords-and-color-protection-v1"
_COLOR = r"(?:white|blue|black|red|green)"
_CONTROLLED_KEYWORDS_AND_COLOR_PROTECTION = re.compile(
    rf"(?P<subject>(?:Other )?creatures you control) have (?P<ordinary>.+?)(?:,\s*|\s+)and\s+"
    rf"(?P<protection>protection from {_COLOR}(?:\s+and from {_COLOR}){{0,4}})\.?$",
    re.IGNORECASE,
)


def fixed_query_keywords_and_color_protection_handler(text: str):
    """Compose two closed grants with exactly the same current recipient set."""
    match = _CONTROLLED_KEYWORDS_AND_COLOR_PROTECTION.fullmatch(text.strip())
    if match is None or _attached_abilities(match["ordinary"]) is None:
        return None
    colors = re.findall(rf"\bfrom ({_COLOR})", match["protection"].casefold())
    if len(set(colors)) != len(colors):
        return None
    from .continuous_templates import fixed_query_keyword_grant_handler

    ordinary = fixed_query_keyword_grant_handler(f"{match['subject']} have {match['ordinary']}.")
    protected = fixed_query_keyword_grant_handler(f"{match['subject']} have {match['protection']}.")
    if ordinary is None or protected is None:
        return None
    return (
        FIXED_KEYWORDS_AND_COLOR_PROTECTION_TEMPLATE,
        (ordinary[1], protected[1]),
        tuple(sorted({*ordinary[2], *protected[2]})),
    )


def _attached_abilities(value: str) -> tuple[str, ...] | None:
    normalized = re.sub(r",?\s+and\s+", ",", value.strip())
    abilities = tuple(
        part.strip().casefold()
        for part in normalized.split(",")
        if part.strip()
    )
    if (
        not abilities
        or len(set(abilities)) != len(abilities)
        or any(
            ability not in _ATTACHED_SUPPORTED_ABILITIES
            and re.fullmatch(r"toxic [1-9]\d*", ability) is None
            for ability in abilities
        )
    ):
        return None
    return tuple(ability.title() for ability in abilities)


def _toxic_ability_fragments(
    abilities: tuple[str, ...],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        ability_fragment_to_dict(
            ToxicSpec(value=int(match.group("value")))
        )
        for ability in abilities
        if (
            match := re.fullmatch(
                r"Toxic (?P<value>[1-9]\d*)",
                ability,
                re.IGNORECASE,
            )
        )
        is not None
    )


def _attached_granted_abilities(
    value: str,
) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]] | None:
    abilities = _attached_abilities(value)
    if abilities is not None:
        return abilities, _toxic_ability_fragments(abilities)
    protection = parse_protection_line(value)
    if protection is not None:
        return (
            ("Protection",),
            tuple(
                ability_fragment_to_dict(fragment)
                for fragment in protection
            ),
        )
    normalized = re.sub(
        r",?\s+and\s+", ",", value.strip(), flags=re.IGNORECASE
    )
    terms = tuple(
        term.strip()
        for term in normalized.rstrip(".").split(",")
        if term.strip()
    )
    if not terms:
        return None
    granted_abilities: list[str] = []
    fragments: list[Mapping[str, Any]] = []
    for term in terms:
        ordinary = _attached_abilities(term)
        if ordinary is not None:
            granted_abilities.extend(ordinary)
            fragments.extend(_toxic_ability_fragments(ordinary))
            continue
        protection = parse_protection_line(term)
        if protection is not None:
            granted_abilities.append("Protection")
            fragments.extend(ability_fragment_to_dict(fragment) for fragment in protection)
            continue
        triggered = triggered_keyword_grant(term)
        if triggered is not None:
            label, fragment, _capability = triggered
            granted_abilities.append(label)
            fragments.append(fragment)
            continue
        ward = fixed_ward_spec(term)
        if ward is None or ward.generic_cost is None:
            return None
        label = f"Ward {{{ward.generic_cost}}}"
        granted_abilities.append(label)
        fragments.append(ability_fragment_to_dict(ward))
    if len(granted_abilities) != len(set(granted_abilities)):
        return None
    return tuple(granted_abilities), tuple(fragments)


def _attached_ability_capabilities(
    abilities: tuple[str, ...],
) -> set[str]:
    capabilities: set[str] = set()
    for ability in abilities:
        if ability == "Protection":
            capabilities.add("protection.typed.debt")
        elif ability.startswith("Toxic "):
            capabilities.add("damage.result.toxic")
        elif ability.startswith("Ward {"):
            capabilities.add("trigger.keyword.ward.fixed_generic")
        elif (triggered := triggered_keyword_grant(ability)) is not None:
            capabilities.add(triggered[2])
        else:
            capabilities.update(
                FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES.get(ability, ())
            )
    return capabilities
