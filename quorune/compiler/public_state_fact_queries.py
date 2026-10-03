from __future__ import annotations

"""Closed non-object public facts for conditional static characteristics."""

import re

from ..characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)
from ..continuous_conditions import (
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
    FixedPublicStateFact,
)
from ..creature_subtypes import canonical_creature_subtype_surface
from ..rules.source_references import SourceReferenceSpec
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


_NUMBERS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}
_COLORS = {"black": "B", "blue": "U", "green": "G", "red": "R", "white": "W"}


def _amount(value: str) -> int | None:
    normalized = value.casefold()
    return int(normalized) if normalized.isdigit() else _NUMBERS.get(normalized)


def _condition(
    fact: FixedPublicStateFact,
    *,
    amount: int = 1,
    parameter: str | None = None,
) -> FixedPublicStateConditionSpec:
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
        amount=amount,
        fact=fact,
        fact_parameter=parameter,
        schema_version=4,
    )


def _query_condition(
    *,
    amount: int,
    scope: CharacteristicQuantityScope,
    query: ObjectQuerySpec,
    at_most: bool = False,
) -> FixedPublicStateConditionSpec:
    return FixedPublicStateConditionSpec(
        (
            FixedPublicStateConditionKind.QUERY_COUNT_AT_MOST
            if at_most
            else FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST
        ),
        amount=amount,
        quantity=CharacteristicQuantitySpec(scope=scope, query=query),
        schema_version=2,
    )


def fixed_planeswalker_condition_query(subject: str) -> ObjectQuerySpec | None:
    match = re.fullmatch(
        r"(?P<subtype>[A-Za-z][A-Za-z'-]*) planeswalker",
        " ".join(subject.strip().split()),
        re.IGNORECASE,
    )
    if match is None:
        return None
    return ObjectQuerySpec(
        zones=("battlefield",),
        types_all=("planeswalker",),
        subtypes_all=(match.group("subtype").casefold(),),
    )


def fixed_graveyard_condition_query(
    normalized: str,
) -> FixedPublicStateConditionSpec | None:
    if re.fullmatch(
        r"there (?:are|is|'s) no cards? in your graveyard",
        normalized,
        re.IGNORECASE,
    ) is not None:
        return _query_condition(
            amount=0,
            scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=("graveyard",)),
            at_most=True,
        )
    presence = re.fullmatch(
        r"(?:a|an) (?P<quality>[A-Za-z][A-Za-z'/-]*) card is in "
        r"your graveyard",
        normalized,
        re.IGNORECASE,
    )
    if presence is None:
        return None
    quality = presence.group("quality").casefold()
    fields: dict[str, object] = {"zones": ("graveyard",)}
    if quality in {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "instant",
        "kindred",
        "land",
        "planeswalker",
        "sorcery",
    }:
        fields["types_all"] = (quality,)
    elif quality in _COLORS:
        fields["colors_all"] = (_COLORS[quality],)
    elif (subtype := canonical_creature_subtype_surface(quality)) is not None:
        fields["subtypes_all"] = (subtype,)
    else:
        return None
    return _query_condition(
        amount=1,
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(**fields),
    )


def fixed_public_condition_query_extension(
    normalized: str,
) -> FixedPublicStateConditionSpec | None:
    attached = re.fullmatch(
        r"(?P<count>two|three|four|five|[0-9]+) or more Equipment are "
        r"attached to it",
        normalized,
        re.IGNORECASE,
    )
    if attached is not None:
        amount = _amount(attached.group("count"))
        assert amount is not None and amount > 0
        return _query_condition(
            amount=amount,
            scope=CharacteristicQuantityScope.ATTACHED_TO_SOURCE,
            query=ObjectQuerySpec(
                zones=("battlefield",),
                types_all=("artifact",),
                subtypes_all=("equipment",),
            ),
        )
    any_player = re.fullmatch(
        r"any player controls (?:a|an) "
        r"(?P<color>black|blue|green|red|white) permanent",
        normalized,
        re.IGNORECASE,
    )
    if any_player is not None:
        return _query_condition(
            amount=1,
            scope=CharacteristicQuantityScope.ALL_ZONES,
            query=ObjectQuerySpec(
                zones=("battlefield",),
                colors_all=(_COLORS[any_player.group("color").casefold()],),
            ),
        )
    global_counter = re.fullmatch(
        r"a creature has a (?P<counter>[A-Za-z0-9+/-]+) counter on it",
        normalized,
        re.IGNORECASE,
    )
    if global_counter is None:
        return None
    return _query_condition(
        amount=1,
        scope=CharacteristicQuantityScope.ALL_ZONES,
        query=ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            state_predicate=PermanentStatePredicateSpec(
                counter_name=global_counter.group("counter"),
                minimum_counter_count=1,
            ),
        ),
    )


def _fixed_scalar_fact(
    normalized: str,
) -> FixedPublicStateConditionSpec | None:
    facts = {
        "you gained life this turn": (
            FixedPublicStateFact.CONTROLLER_LIFE_GAINED_THIS_TURN,
            1,
        ),
        "you lost life this turn": (
            FixedPublicStateFact.CONTROLLER_LIFE_LOST_THIS_TURN,
            1,
        ),
        "you sacrificed a permanent this turn": (
            FixedPublicStateFact.CONTROLLER_PERMANENTS_SACRIFICED_THIS_TURN,
            1,
        ),
        "another creature entered the battlefield under your control this turn": (
            FixedPublicStateFact.CONTROLLER_OTHER_CREATURES_ENTERED_THIS_TURN,
            1,
        ),
        "an artifact entered the battlefield under your control this turn": (
            FixedPublicStateFact.CONTROLLER_ARTIFACTS_ENTERED_THIS_TURN,
            1,
        ),
        "two or more nonland permanents entered the battlefield under your control this turn": (
            FixedPublicStateFact.CONTROLLER_NONLAND_PERMANENTS_ENTERED_THIS_TURN,
            2,
        ),
        "two or more creatures died under your control this turn": (
            FixedPublicStateFact.CONTROLLER_CREATURES_DIED_THIS_TURN,
            2,
        ),
        "you have more cards in hand than each opponent": (
            FixedPublicStateFact.CONTROLLER_HAND_ADVANTAGE,
            1,
        ),
        "your life total is less than or equal to half your starting life total": (
            FixedPublicStateFact.CONTROLLER_AT_OR_BELOW_HALF_STARTING_LIFE,
            1,
        ),
        "an instant card and a sorcery card are in your graveyard": (
            FixedPublicStateFact.CONTROLLER_GRAVEYARD_HAS_INSTANT_AND_SORCERY,
            1,
        ),
    }
    parsed = facts.get(normalized.casefold())
    return _condition(parsed[0], amount=parsed[1]) if parsed is not None else None


def _fixed_history_fact(normalized: str) -> FixedPublicStateConditionSpec | None:
    facts = {
        "a creature died this turn": FixedPublicStateFact.ANY_CREATURE_DIED_THIS_TURN,
        "you attacked this turn": FixedPublicStateFact.CONTROLLER_ATTACKED_THIS_TURN,
        "a permanent left the battlefield under your control this turn": FixedPublicStateFact.CONTROLLER_PERMANENT_LEFT_THIS_TURN,
        "a permanent you controlled left the battlefield this turn": FixedPublicStateFact.CONTROLLER_PERMANENT_LEFT_THIS_TURN,
        "an opponent lost life this turn": FixedPublicStateFact.OPPONENT_LOST_LIFE_THIS_TURN,
    }
    fact = facts.get(normalized.casefold())
    if fact is not None:
        return _condition(fact)
    life = re.fullmatch(
        rf"you (?P<verb>gained|lost) (?P<amount>{FIXED_COUNT_PATTERN}|[0-9]+) or more life this turn",
        normalized, re.IGNORECASE,
    )
    if life is None:
        return None
    amount = fixed_number(life.group("amount"))
    if amount is None or amount <= 0:
        return None
    return _condition(
        FixedPublicStateFact.CONTROLLER_LIFE_GAINED_THIS_TURN
        if life.group("verb").casefold() == "gained"
        else FixedPublicStateFact.CONTROLLER_LIFE_LOST_THIS_TURN,
        amount=amount,
    )


def fixed_public_fact_condition(
    text: str,
    *,
    source_name: str,
) -> FixedPublicStateConditionSpec | None:
    """Parse one closed current public-fact threshold."""

    normalized = " ".join(text.strip().rstrip(".").split())
    distinct = re.fullmatch(
        r"there are (?P<count>four|five|[0-9]+) or more "
        r"(?P<field>card types|mana values) among cards in your graveyard",
        normalized,
        re.IGNORECASE,
    )
    if distinct is not None:
        amount = _amount(distinct.group("count"))
        assert amount is not None and amount > 0
        return _condition(
            (
                FixedPublicStateFact.CONTROLLER_GRAVEYARD_DISTINCT_CARD_TYPES
                if distinct.group("field").casefold() == "card types"
                else FixedPublicStateFact.CONTROLLER_GRAVEYARD_DISTINCT_MANA_VALUES
            ),
            amount=amount,
        )
    scalar = _fixed_scalar_fact(normalized)
    if scalar is not None:
        return scalar
    history = _fixed_history_fact(normalized)
    if history is not None:
        return history
    above_start = re.fullmatch(
        r"you have at least (?P<amount>[0-9]+) life more than your starting "
        r"life total",
        normalized,
        re.IGNORECASE,
    )
    if above_start is not None:
        return _condition(
            FixedPublicStateFact.CONTROLLER_LIFE_ABOVE_STARTING,
            amount=int(above_start.group("amount")),
        )
    source = SourceReferenceSpec(source_name).regex_pattern
    attacked = re.fullmatch(
        rf"(?:it|This creature|This permanent|{source}) attacked"
        rf"(?P<battle> a battle)? this turn",
        normalized,
        re.IGNORECASE,
    )
    if attacked is not None:
        return _condition(
            (
                FixedPublicStateFact.SOURCE_ATTACKED_BATTLE_THIS_TURN
                if attacked.group("battle")
                else FixedPublicStateFact.SOURCE_ATTACKED_THIS_TURN
            )
        )
    subtype_attack = re.fullmatch(
        r"you attacked with (?P<count>two|three|four|five|[0-9]+) or more "
        r"(?P<subtype>[A-Za-z][A-Za-z'-]*) this turn",
        normalized,
        re.IGNORECASE,
    )
    if subtype_attack is None:
        return None
    amount = _amount(subtype_attack.group("count"))
    assert amount is not None and amount > 0
    subtype = canonical_creature_subtype_surface(
        subtype_attack.group("subtype")
    )
    if subtype is None:
        return None
    return _condition(
        FixedPublicStateFact.CONTROLLER_ATTACKED_WITH_SUBTYPE_THIS_TURN,
        amount=amount,
        parameter=subtype,
    )


__all__ = [
    "fixed_graveyard_condition_query",
    "fixed_planeswalker_condition_query",
    "fixed_public_condition_query_extension",
    "fixed_public_fact_condition",
]
