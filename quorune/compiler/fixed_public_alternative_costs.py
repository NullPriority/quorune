from __future__ import annotations

"""Closed Oracle grammar for fixed public alternative casting costs."""

from . import regex_working_set as re
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
    ANNOUNCED_X_PITCH_PRICE_VERSION,
    LIFE_AND_BLUE_HAND_EXILE_PRICE_VERSION,
    life_and_blue_hand_exile_payments,
    BATTLEFIELD_CREATURE_PRICE_VERSION,
    FIXED_PUBLIC_ALTERNATIVE_COST_MECHANIC,
    OPPONENT_HISTORY_COSTS,
    CREATURE_DAMAGE_PRICE_VERSION,
    LIVE_ATTACKING_COSTS,
    LIVE_ATTACKING_PRICE_VERSION,
    OPPONENT_HISTORY_PRICE_VERSION,
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
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


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
_LIFE_AND_BLUE_HAND_EXILE = re.compile(
    r"You may pay 1 life and exile a blue card from your hand rather than pay this spell['’]s mana cost\.?",
    re.IGNORECASE,
)
_ANNOUNCED_X_PITCH = re.compile(
    r"You may exile a (?P<color>white|blue|black|red|green) card with mana value X "
    r"from your hand rather than pay this spell['’]s mana cost\.?",
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


_OPPONENT_HISTORY_LINES = {
    "if an opponent drew three or more cards this turn": FixedPublicAlternativeConditionKind.OPPONENT_DREW_THREE,
    "if an opponent gained life this turn": FixedPublicAlternativeConditionKind.OPPONENT_GAINED_LIFE,
    "if an opponent had two or more lands enter the battlefield under their control this turn":
        FixedPublicAlternativeConditionKind.OPPONENT_LAND_ARRIVALS,
    "if an opponent cast a blue spell this turn": FixedPublicAlternativeConditionKind.OPPONENT_BLUE_CAST,
    "if an opponent cast three or more spells this turn":
        FixedPublicAlternativeConditionKind.OPPONENT_CAST_THREE_SPELLS,
    "if a creature spell you cast this turn was countered by a spell or ability an opponent controlled":
        FixedPublicAlternativeConditionKind.OPPONENT_COUNTERED_CREATURE_CAST,
    "if an opponent had three or more cards put into their graveyard from anywhere this turn":
        FixedPublicAlternativeConditionKind.OPPONENT_GRAVEYARD_ARRIVALS,
    "if an opponent had two or more creatures enter the battlefield under their control this turn":
        FixedPublicAlternativeConditionKind.OPPONENT_CREATURE_ARRIVALS,
}


_FIXED_ALTERNATIVE_PRICE_LINE = re.compile(
    r"(?P<condition>If [^,]+), you may pay (?P<cost>(?:\{[0-9WUBRGC]+\})+) "
    r"rather than pay this spell['’]s mana cost\.?",
    re.I,
)


def _opponent_history_price(material_line, oracle_line, line_index):
    match = _FIXED_ALTERNATIVE_PRICE_LINE.fullmatch(material_line.strip())
    if match is None:
        return None
    kind = _OPPONENT_HISTORY_LINES.get(match["condition"].casefold())
    if kind is None or match["cost"].upper() != OPPONENT_HISTORY_COSTS[kind]:
        return None
    cost_text, mana = _mana(OPPONENT_HISTORY_COSTS[kind])
    return FixedPublicAlternativeCostSpec(
        ability_id=f"ab{line_index + 1}", line_index=line_index, oracle_line=oracle_line,
        kind=FixedPublicAlternativeCostKind.PLAIN,
        condition=FixedPublicAlternativeCondition(kind, schema_version=OPPONENT_HISTORY_PRICE_VERSION),
        cost_text=cost_text, mana_requirements=mana,
    )


_LIVE_ATTACKING_LINES = {
    "if exactly one creature is attacking": FixedPublicAlternativeConditionKind.EXACTLY_ONE_ATTACKING_CREATURE,
    "if three or more creatures are attacking": FixedPublicAlternativeConditionKind.AT_LEAST_THREE_ATTACKING_CREATURES,
    "if four or more creatures are attacking": FixedPublicAlternativeConditionKind.AT_LEAST_FOUR_ATTACKING_CREATURES,
}


def _live_attacking_price(material_line, oracle_line, line_index):
    match = _FIXED_ALTERNATIVE_PRICE_LINE.fullmatch(material_line.strip())
    if match is None:
        return None
    kind = _LIVE_ATTACKING_LINES.get(match["condition"].casefold())
    if kind is None or match["cost"].upper() != LIVE_ATTACKING_COSTS[kind]:
        return None
    cost_text, mana = _mana(LIVE_ATTACKING_COSTS[kind])
    return FixedPublicAlternativeCostSpec(
        ability_id=f"ab{line_index + 1}", line_index=line_index, oracle_line=oracle_line,
        kind=FixedPublicAlternativeCostKind.PLAIN,
        condition=FixedPublicAlternativeCondition(kind, schema_version=LIVE_ATTACKING_PRICE_VERSION),
        cost_text=cost_text, mana_requirements=mana,
    )


def _creature_damage_price(material_line, oracle_line, line_index):
    match = _FIXED_ALTERNATIVE_PRICE_LINE.fullmatch(material_line.strip())
    if (match is None or match["condition"].casefold().replace("’", "'")
            != "if you've been dealt damage by two or more creatures this turn"
            or match["cost"].upper() != "{R}"):
        return None
    cost_text, mana = _mana("{R}")
    return FixedPublicAlternativeCostSpec(
        ability_id=f"ab{line_index + 1}", line_index=line_index, oracle_line=oracle_line,
        kind=FixedPublicAlternativeCostKind.PLAIN,
        condition=FixedPublicAlternativeCondition(
            FixedPublicAlternativeConditionKind.DAMAGED_BY_TWO_CREATURES,
            schema_version=CREATURE_DAMAGE_PRICE_VERSION,
        ),
        cost_text=cost_text, mana_requirements=mana,
    )


_BATTLEFIELD_CREATURE_PRICE = re.compile(
    rf"You may pay (?P<mana>{_ORDINARY_COST}) rather than pay this spell['’]s mana cost "
    rf"if there are (?P<count>thirteen|{FIXED_COUNT_PATTERN}) or more creatures on the battlefield\.",
    re.IGNORECASE,
)


def _battlefield_creature_price(material_line, oracle_line, line_index):
    match = _BATTLEFIELD_CREATURE_PRICE.fullmatch(material_line.strip())
    if match is None:
        return None
    minimum = _count(match["count"])
    payment = _mana(match["mana"])
    if minimum is None or payment is None:
        return None
    cost_text, mana = payment
    return FixedPublicAlternativeCostSpec(
        ability_id=f"ab{line_index + 1}", line_index=line_index, oracle_line=oracle_line,
        kind=FixedPublicAlternativeCostKind.PLAIN,
        condition=FixedPublicAlternativeCondition(
            FixedPublicAlternativeConditionKind.MINIMUM_BATTLEFIELD_CREATURES,
            schema_version=BATTLEFIELD_CREATURE_PRICE_VERSION, minimum_creature_count=minimum,
        ),
        cost_text=cost_text, mana_requirements=mana,
    )


def compile_fixed_public_alternative_cost(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> FixedPublicAlternativeCostSpec | None:
    """Parse one closed fixed alternative-cost declaration."""
    from .conditional_free_casts import conditional_zero_mana_condition

    if _LIFE_AND_BLUE_HAND_EXILE.fullmatch(material_line.strip()):
        return FixedPublicAlternativeCostSpec(
            ability_id=f"ab{line_index + 1}", line_index=line_index, oracle_line=oracle_line,
            kind=FixedPublicAlternativeCostKind.PLAIN,
            condition=FixedPublicAlternativeCondition(FixedPublicAlternativeConditionKind.ALWAYS),
            cost_text=None, mana_requirements=_zero_mana(),
            additional_costs=life_and_blue_hand_exile_payments(),
            schema_version=LIFE_AND_BLUE_HAND_EXILE_PRICE_VERSION,
        )
    if match := _ANNOUNCED_X_PITCH.fullmatch(material_line.strip()):
        cost = FixedZoneChangeAdditionalCost(
            operation=EXILE_ONE_FROM_HAND_COST, count=1, choice_field="exile_cards",
            predicate=_hand_query(match["color"]),
        )
        return FixedPublicAlternativeCostSpec(
            ability_id=f"ab{line_index + 1}", line_index=line_index, oracle_line=oracle_line,
            kind=FixedPublicAlternativeCostKind.PLAIN,
            condition=FixedPublicAlternativeCondition(FixedPublicAlternativeConditionKind.ALWAYS),
            cost_text=None, mana_requirements=_zero_mana(),
            additional_costs=(FrozenMap(cost.to_descriptor()),),
            schema_version=ANNOUNCED_X_PITCH_PRICE_VERSION,
        )
    history_price = _opponent_history_price(material_line, oracle_line, line_index)
    if history_price is not None:
        return history_price
    attacking_price = _live_attacking_price(material_line, oracle_line, line_index)
    if attacking_price is not None:
        return attacking_price
    battlefield_price = _battlefield_creature_price(material_line, oracle_line, line_index)
    if battlefield_price is not None:
        return battlefield_price
    damage_price = _creature_damage_price(material_line, oracle_line, line_index)
    if damage_price is not None:
        return damage_price
    zero_condition = conditional_zero_mana_condition(material_line)
    if zero_condition is not None:
        return FixedPublicAlternativeCostSpec(ability_id=f'ab{line_index+1}', line_index=line_index,
            oracle_line=oracle_line, kind=FixedPublicAlternativeCostKind.PLAIN, condition=zero_condition,
            cost_text='{0}', mana_requirements=_zero_mana())
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
