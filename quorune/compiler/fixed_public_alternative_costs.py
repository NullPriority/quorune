from __future__ import annotations

"""Closed Oracle grammar for fixed public alternative casting costs."""

import re
from typing import Any

from ..additional_cost_vocabulary import (
    DISCARD_ONE_COST,
    EXILE_ONE_FROM_GRAVEYARD_COST,
    EXILE_ONE_FROM_HAND_COST,
    RETURN_ONE_TO_OWNER_HAND_COST,
    SACRIFICE_ONE_COST,
)
from ..object_predicate import ObjectQuerySpec
from ..public_alternative_costs import (
    FIXED_PUBLIC_ALTERNATIVE_COST_MECHANIC,
    FixedPublicAlternativeCondition,
    FixedPublicAlternativeConditionKind,
    FixedPublicAlternativeCostKind,
    FixedPublicAlternativeCostSpec,
)
from ..replacement.immutable import FrozenMap
from ..rules.casting_additional_cost_groups import (
    FixedLifePaymentAdditionalCost,
)
from ..rules.casting_additional_costs import FixedZoneChangeAdditionalCost
from ..util import mana_cost_to_vector
from .fixed_numbers import fixed_number


_MANA_KEYS = ("GENERIC", "W", "U", "B", "R", "G", "C")
_ORDINARY_COST = r"(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+"
_KEYWORD = re.compile(
    rf"^(?P<kind>Freerunning|Prowl|Spectacle|Surge) "
    rf"(?P<cost>{_ORDINARY_COST})(?:\s+\(.*\))?\.?$",
    re.IGNORECASE,
)
_PLAIN = re.compile(
    r"^(?:(?P<condition>If it's not your turn|If it's your turn|"
    r"If you control an? (?P<land>Forest|Island|Mountain|Plains|Swamp)), )?"
    r"You may (?P<payment>.+?) rather than pay this spell's mana cost\.?$",
    re.IGNORECASE,
)
_MANA_ONLY = re.compile(rf"pay (?P<mana>{_ORDINARY_COST})", re.IGNORECASE)
_LIFE_ONLY = re.compile(r"pay (?P<count>[1-9][0-9]*) life", re.IGNORECASE)
_MANA_AND_RETURN = re.compile(
    rf"pay (?P<mana>{_ORDINARY_COST}) and return a basic land you control "
    r"to its owner's hand",
    re.IGNORECASE,
)
_MANA_AND_EXILE = re.compile(
    rf"pay (?P<mana>{_ORDINARY_COST}) and exile a creature card "
    r"from your graveyard",
    re.IGNORECASE,
)
_HAND_MOVE = re.compile(
    r"(?P<verb>discard|exile) (?P<count>a|an|one|two|three|four|five|[1-9][0-9]*) "
    r"(?P<quality>white|blue|black|red|green|Forest|Island|Mountain|Plains|Swamp) "
    r"cards?(?P<from_hand> from your hand)?",
    re.IGNORECASE,
)
_RETURN = re.compile(
    r"return (?P<count>a|an|one|two|three|four|five|[1-9][0-9]*) "
    r"(?P<quality>basic land|Forests?|Islands?|Mountains?|Plains|Swamps?) "
    r"you control to (?:its|their) owner's hand",
    re.IGNORECASE,
)
_SACRIFICE = re.compile(
    r"sacrifice (?P<count>a|an|one|two|three|four|five|[1-9][0-9]*) "
    r"(?P<nontoken>nontoken )?"
    r"(?:(?P<color>white|blue|black|red|green|colorless) )?"
    r"(?P<quality>artifacts?|creatures?|lands?|permanents?|Forests?|Islands?|"
    r"Mountains?|Plains|Swamps?|Eldrazi Spawn)",
    re.IGNORECASE,
)
_COLORS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
    "colorless": "C",
}
_BASIC_LAND_TYPE_NORMALIZATION = {
    "forest": "forest",
    "forests": "forest",
    "island": "island",
    "islands": "island",
    "mountain": "mountain",
    "mountains": "mountain",
    "plains": "plains",
    "swamp": "swamp",
    "swamps": "swamp",
}
_PERMANENT_TYPE_NORMALIZATION = {
    "artifact": "artifact",
    "artifacts": "artifact",
    "creature": "creature",
    "creatures": "creature",
    "land": "land",
    "lands": "land",
    "permanent": "permanent",
    "permanents": "permanent",
}


def _count(value: str) -> int | None:
    normalized = value.casefold()
    if normalized in {"a", "an"}:
        return 1
    parsed = fixed_number(normalized)
    return parsed if parsed is not None and 1 <= parsed <= 99 else None


def _mana(text: str) -> tuple[str, tuple[tuple[str, int], ...]] | None:
    normalized = text.upper()
    requirements, complex_symbols = mana_cost_to_vector(normalized)
    if complex_symbols:
        return None
    return normalized, tuple((key, requirements[key]) for key in _MANA_KEYS)


def _zero_mana() -> tuple[tuple[str, int], ...]:
    return tuple((key, 0) for key in _MANA_KEYS)


def _condition(
    kind: FixedPublicAlternativeCostKind,
    match: re.Match[str] | None = None,
) -> FixedPublicAlternativeCondition:
    if kind is FixedPublicAlternativeCostKind.FREERUNNING:
        value = FixedPublicAlternativeConditionKind.FREERUNNING
    elif kind is FixedPublicAlternativeCostKind.PROWL:
        value = FixedPublicAlternativeConditionKind.PROWL
    elif kind is FixedPublicAlternativeCostKind.SPECTACLE:
        value = FixedPublicAlternativeConditionKind.OPPONENT_LOST_LIFE
    elif kind is FixedPublicAlternativeCostKind.SURGE:
        value = (
            FixedPublicAlternativeConditionKind.CONTROLLER_CAST_ANOTHER_SPELL
        )
    elif match is None or not match.group("condition"):
        value = FixedPublicAlternativeConditionKind.ALWAYS
    elif match.group("land"):
        return FixedPublicAlternativeCondition(
            kind=FixedPublicAlternativeConditionKind.CONTROLS_BASIC_LAND_TYPE,
            basic_land_type=match.group("land").casefold(),
        )
    elif "not your turn" in match.group("condition").casefold():
        value = FixedPublicAlternativeConditionKind.NOT_YOUR_TURN
    else:
        value = FixedPublicAlternativeConditionKind.YOUR_TURN
    return FixedPublicAlternativeCondition(kind=value)


def _hand_query(quality: str) -> ObjectQuerySpec:
    normalized = quality.casefold()
    values: dict[str, Any] = {}
    if normalized in _COLORS:
        values["colors_all"] = (_COLORS[normalized],)
    else:
        values["subtypes_all"] = (normalized,)
    return ObjectQuerySpec(
        zones=("hand",),
        owner="$actor",
        known_to_actor=True,
        **values,
    )


def _return_query(quality: str) -> ObjectQuerySpec:
    normalized = quality.casefold()
    if normalized == "basic land":
        return ObjectQuerySpec(
            zones=("battlefield",),
            controller="$actor",
            types_all=("land",),
            supertypes_all=("basic",),
            known_to_actor=True,
        )
    return ObjectQuerySpec(
        zones=("battlefield",),
        controller="$actor",
        subtypes_all=(_BASIC_LAND_TYPE_NORMALIZATION[normalized],),
        known_to_actor=True,
    )


def _sacrifice_query(match: re.Match[str]) -> ObjectQuerySpec:
    quality = match.group("quality").casefold()
    values: dict[str, Any] = {}
    if quality in {"permanent", "permanents"}:
        pass
    elif quality in _PERMANENT_TYPE_NORMALIZATION:
        values["types_all"] = (_PERMANENT_TYPE_NORMALIZATION[quality],)
    elif quality == "eldrazi spawn":
        values["types_all"] = ("creature",)
        values["subtypes_all"] = ("eldrazi", "spawn")
    elif quality in _BASIC_LAND_TYPE_NORMALIZATION:
        values["types_all"] = ("land",)
        values["subtypes_all"] = (
            _BASIC_LAND_TYPE_NORMALIZATION[quality],
        )
    if match.group("color"):
        values["colors_all"] = (_COLORS[match.group("color").casefold()],)
    return ObjectQuerySpec(
        zones=("battlefield",),
        controller="$actor",
        token=False if match.group("nontoken") else None,
        known_to_actor=True,
        **values,
    )


def _payment(
    text: str,
) -> tuple[
    str | None,
    tuple[tuple[str, int], ...],
    tuple[FrozenMap, ...],
] | None:
    normalized = " ".join(text.strip().split())
    if match := _MANA_ONLY.fullmatch(normalized):
        parsed = _mana(match.group("mana"))
        return (parsed[0], parsed[1], ()) if parsed is not None else None
    if match := _LIFE_ONLY.fullmatch(normalized):
        amount = int(match.group("count"))
        return (
            None,
            _zero_mana(),
            (FrozenMap(FixedLifePaymentAdditionalCost(amount).to_descriptor()),),
        )
    if match := _MANA_AND_RETURN.fullmatch(normalized):
        parsed = _mana(match.group("mana"))
        if parsed is None:
            return None
        cost = FixedZoneChangeAdditionalCost(
            operation=RETURN_ONE_TO_OWNER_HAND_COST,
            choice_field="return_cards",
            predicate=_return_query("basic land"),
        )
        return parsed[0], parsed[1], (FrozenMap(cost.to_descriptor()),)
    if match := _MANA_AND_EXILE.fullmatch(normalized):
        parsed = _mana(match.group("mana"))
        if parsed is None:
            return None
        cost = FixedZoneChangeAdditionalCost(
            operation=EXILE_ONE_FROM_GRAVEYARD_COST,
            choice_field="exile_cards",
            predicate=ObjectQuerySpec(
                zones=("graveyard",),
                owner="$actor",
                types_all=("creature",),
                known_to_actor=True,
            ),
        )
        return parsed[0], parsed[1], (FrozenMap(cost.to_descriptor()),)
    if match := _HAND_MOVE.fullmatch(normalized):
        count = _count(match.group("count"))
        verb = match.group("verb").casefold()
        if count is None or (verb == "exile") is not bool(match.group("from_hand")):
            return None
        operation = (
            EXILE_ONE_FROM_HAND_COST if verb == "exile" else DISCARD_ONE_COST
        )
        cost = FixedZoneChangeAdditionalCost(
            operation=operation,
            count=count,
            choice_field="exile_cards" if verb == "exile" else "discard_cards",
            predicate=_hand_query(match.group("quality")),
        )
        return None, _zero_mana(), (FrozenMap(cost.to_descriptor()),)
    if match := _RETURN.fullmatch(normalized):
        count = _count(match.group("count"))
        if count is None:
            return None
        cost = FixedZoneChangeAdditionalCost(
            operation=RETURN_ONE_TO_OWNER_HAND_COST,
            count=count,
            choice_field="return_cards",
            predicate=_return_query(match.group("quality")),
        )
        return None, _zero_mana(), (FrozenMap(cost.to_descriptor()),)
    if match := _SACRIFICE.fullmatch(normalized):
        count = _count(match.group("count"))
        if count is None:
            return None
        cost = FixedZoneChangeAdditionalCost(
            operation=SACRIFICE_ONE_COST,
            count=count,
            choice_field="sacrifice_cards",
            predicate=_sacrifice_query(match),
        )
        return None, _zero_mana(), (FrozenMap(cost.to_descriptor()),)
    return None


def compile_fixed_public_alternative_cost(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> FixedPublicAlternativeCostSpec | None:
    """Parse one closed fixed alternative-cost declaration."""

    keyword = _KEYWORD.fullmatch(material_line.strip())
    if keyword is not None:
        kind = FixedPublicAlternativeCostKind(
            keyword.group("kind").casefold()
        )
        payment = _mana(keyword.group("cost"))
        if payment is None:
            return None
        cost_text, mana = payment
        additional: tuple[FrozenMap, ...] = ()
        condition = _condition(kind)
    else:
        plain = _PLAIN.fullmatch(material_line.strip())
        if plain is None:
            return None
        kind = FixedPublicAlternativeCostKind.PLAIN
        parsed = _payment(plain.group("payment"))
        if parsed is None:
            return None
        cost_text, mana, additional = parsed
        condition = _condition(kind, plain)
    return FixedPublicAlternativeCostSpec(
        ability_id=f"ab{line_index + 1}",
        line_index=line_index,
        oracle_line=oracle_line,
        kind=kind,
        condition=condition,
        cost_text=cost_text,
        mana_requirements=mana,
        additional_costs=additional,
    )


__all__ = [
    "FIXED_PUBLIC_ALTERNATIVE_COST_MECHANIC",
    "compile_fixed_public_alternative_cost",
]
