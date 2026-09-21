from __future__ import annotations

"""Closed Oracle grammar for battlefield-static casting rules."""

import re
from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..enchant_spec import SimpleEnchantSpec, enchant_spec_to_dict
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import SourceReferenceSpec
from ..semantic_runtime.static_cast_rules import (
    STATIC_CAST_LIMIT_HANDLER_ID,
    STATIC_CAST_PROHIBITION_HANDLER_ID,
    STATIC_CAST_RULE_EVENT,
    STATIC_CAST_TIMING_HANDLER_ID,
    STATIC_UNCOUNTERABLE_HANDLER_ID,
    StaticCastCondition,
    StaticCastPlayerScope,
    StaticCastRuleKind,
)
from .action_permission_templates import public_spell_queries


StaticCastRuleTemplate = tuple[
    str,
    Mapping[str, Any],
    str | tuple[str, ...],
]


_HISTORIC_REMINDER = " (Artifacts, legendaries, and Sagas are historic.)"
_FLASH_REMINDERS = (
    " (You may cast them any time you could cast an instant.)",
    " (You may cast it any time you could cast an instant.)",
)
_TIMING = re.compile(
    r"^(?P<actor>You|Any player|Players) may cast (?P<subject>.+?) "
    r"as though (?P<pronoun>they|it) had flash\.?$",
    re.IGNORECASE,
)
_PERIOD_PROHIBITION = re.compile(
    r"^(?P<actor>Your opponents|Players) can't cast spells during "
    r"(?P<period>combat|your turn)\.?$",
    re.IGNORECASE,
)
_KIND_PROHIBITION = re.compile(
    r"^(?P<actor>You|Your opponents|Players) can't cast "
    r"(?P<subject>permanent|noncreature|creature|artifact|enchantment|"
    r"instant|sorcery|blue creature) spells\.?$",
    re.IGNORECASE,
)
_ZONE_PROHIBITION = re.compile(
    r"^Players can't cast spells from graveyards or libraries\.?$",
    re.IGNORECASE,
)
_CAST_LIMIT = re.compile(
    r"^(?P<actor>Each player|You|Your opponents) can't cast more than "
    r"one (?P<subject>spell|noncreature spell) "
    r"each turn\.?$",
    re.IGNORECASE,
)
_ENCHANTED_PLAYER_CAST_LIMIT = re.compile(
    r"^Enchanted player can't cast more than one spell each turn\.?$",
    re.IGNORECASE,
)
_CHOSEN_NAME_PROHIBITION = re.compile(
    r"^(?:(?P<opponents>Your opponents) can't cast spells with the chosen "
    r"name|Spells with the chosen name can't be cast)\.?$",
    re.IGNORECASE,
)
_UNCOUNTERABLE = re.compile(
    r"^(?P<subject>Spells|.+? spells?)(?P<relation> you control| you cast)? "
    r"can't be countered\.?$",
    re.IGNORECASE,
)
_SPELLS_AND_ABILITIES_UNCOUNTERABLE = re.compile(
    r"^Spells and abilities can't be countered\.?$",
    re.IGNORECASE,
)


def _query(**kwargs: Any) -> dict[str, Any]:
    excluded = tuple(kwargs.pop("excluded_types", ()))
    return ObjectQuerySpec(
        **kwargs,
        excluded_types=tuple(dict.fromkeys((*excluded, "land"))),
    ).to_dict()


def _queries(subject: str) -> tuple[dict[str, Any], ...] | None:
    normalized = " ".join(subject.casefold().split())
    if normalized in {"spell", "spells"}:
        return (_query(),)
    normalized = " ".join(re.sub(r"\bspells?\b", " ", normalized).split())
    if normalized == "aura spells with enchant creature":
        return (
            _query(types_all=("enchantment",), subtypes_all=("aura",)),
        )
    if normalized == "aura with enchant creature":
        return (
            _query(types_all=("enchantment",), subtypes_all=("aura",)),
        )
    direct = public_spell_queries(subject)
    if direct is not None:
        return direct
    terms = tuple(
        part.strip()
        for part in re.sub(
            r",?\s+(?:and/or|and|or)\s+",
            ",",
            normalized,
        ).split(",")
        if part.strip()
    )
    if not terms or len(terms) > 4:
        return None
    colors = {
        "white": "W",
        "blue": "U",
        "black": "B",
        "red": "R",
        "green": "G",
    }
    card_types = {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "instant",
        "kindred",
        "planeswalker",
        "sorcery",
    }
    permanent_types = tuple(
        sorted(card_types - {"instant", "kindred", "sorcery"})
    )
    queries: list[dict[str, Any]] = []
    for term in terms:
        colored_creature = re.fullmatch(
            r"(white|blue|black|red|green) creature",
            term,
        )
        if colored_creature is not None:
            queries.append(
                _query(
                    types_all=("creature",),
                    colors_any=(colors[colored_creature.group(1)],),
                )
            )
        elif term in colors:
            queries.append(_query(colors_any=(colors[term],)))
        elif term == "colorless":
            queries.append(_query(colorless=True))
        elif term == "legendary":
            queries.append(_query(supertypes_all=("legendary",)))
        elif term == "permanent":
            queries.append(_query(types_any=permanent_types))
        elif term == "noncreature":
            queries.append(_query(excluded_types=("creature",)))
        elif term in card_types:
            queries.append(_query(types_all=(term,)))
        elif term in {"aura", "equipment"}:
            queries.append(_query(subtypes_all=(term,)))
        else:
            subtype = canonical_creature_subtype(term)
            if subtype is None:
                return None
            queries.append(_query(subtypes_all=(subtype,)))
    return tuple(queries)


def _scope(value: str) -> StaticCastPlayerScope:
    normalized = value.casefold()
    if normalized == "you":
        return StaticCastPlayerScope.SOURCE_CONTROLLER
    if normalized == "your opponents":
        return StaticCastPlayerScope.SOURCE_OPPONENTS
    return StaticCastPlayerScope.ALL_PLAYERS


def _descriptor(
    *,
    handler_id: str,
    kind: StaticCastRuleKind,
    scope: StaticCastPlayerScope,
    queries: tuple[dict[str, Any], ...],
    condition: StaticCastCondition = StaticCastCondition.ALWAYS,
    origin_zones: tuple[str, ...] = (),
    maximum_per_turn: int | None = None,
    chosen_name: bool = False,
    affects_abilities: bool = False,
    required_enchant_spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "handler_id": handler_id,
        "schema_version": 1,
        "event": STATIC_CAST_RULE_EVENT,
        "kind": kind.value,
        "player_scope": scope.value,
        "spell_queries": list(queries),
        "condition": condition.value,
        "origin_zones": list(origin_zones),
        "maximum_per_turn": maximum_per_turn,
        "chosen_name": chosen_name,
        "affects_abilities": affects_abilities,
        "required_enchant_spec": (
            dict(required_enchant_spec)
            if required_enchant_spec is not None
            else None
        ),
    }


def _without_exact_reminder(text: str) -> str:
    normalized = text.strip()
    for reminder in (*_FLASH_REMINDERS, _HISTORIC_REMINDER):
        if normalized.endswith(reminder):
            normalized = normalized[: -len(reminder)]
    return normalized


def _timing_rule(
    normalized: str,
    *,
    source_name: str,
) -> StaticCastRuleTemplate | None:
    timing = _TIMING.fullmatch(normalized)
    if timing is not None and timing.group("pronoun").casefold() == "they":
        subject = timing.group("subject")
        queries = _queries(subject)
        if queries is not None:
            required_enchant_spec = (
                enchant_spec_to_dict(SimpleEnchantSpec("creature"))
                if " ".join(subject.casefold().split())
                == "aura spells with enchant creature"
                else None
            )
            return (
                "static-cast-timing-fixed-query-v1",
                _descriptor(
                    handler_id=STATIC_CAST_TIMING_HANDLER_ID,
                    kind=StaticCastRuleKind.TIMING_PERMISSION,
                    scope=_scope(timing.group("actor")),
                    queries=queries,
                    required_enchant_spec=required_enchant_spec,
                ),
                "casting.timing.fixed_query_static",
            )
    conditional_timing = [
        (
            re.fullmatch(
                r"Cosmic Awareness — As long as an opponent has cast a spell "
                r"this turn, you may cast spells as though they had flash\.?",
                normalized,
                re.IGNORECASE,
            ),
            StaticCastCondition.OPPONENT_CAST_SPELL_THIS_TURN,
        ),
        (
            re.fullmatch(
                r"During each opponent's end step, you may cast spells as "
                r"though they had flash\.?",
                normalized,
                re.IGNORECASE,
            ),
            StaticCastCondition.OPPONENT_END_STEP,
        ),
    ]
    if source_name.strip():
        source_pattern = SourceReferenceSpec(source_name).regex_pattern
        conditional_timing.append(
            (
                re.fullmatch(
                    rf"As long as {source_pattern} is tapped, you may cast "
                    r"spells as though they had flash\.?",
                    normalized,
                    re.IGNORECASE,
                ),
                StaticCastCondition.SOURCE_TAPPED,
            )
        )
    for match, condition in conditional_timing:
        if match is not None:
            return (
                "static-cast-timing-public-condition-v1",
                _descriptor(
                    handler_id=STATIC_CAST_TIMING_HANDLER_ID,
                    kind=StaticCastRuleKind.TIMING_PERMISSION,
                    scope=StaticCastPlayerScope.SOURCE_CONTROLLER,
                    queries=(_query(),),
                    condition=condition,
                ),
                "casting.timing.fixed_query_static",
            )
    return None


def _cast_prohibition_or_limit_rule(
    normalized: str,
) -> StaticCastRuleTemplate | None:

    period = _PERIOD_PROHIBITION.fullmatch(normalized)
    if period is not None:
        return (
            "static-cast-period-prohibition-v1",
            _descriptor(
                handler_id=STATIC_CAST_PROHIBITION_HANDLER_ID,
                kind=StaticCastRuleKind.CAST_PROHIBITION,
                scope=_scope(period.group("actor")),
                queries=(_query(),),
                condition=(
                    StaticCastCondition.COMBAT
                    if period.group("period").casefold() == "combat"
                    else StaticCastCondition.SOURCE_CONTROLLER_TURN
                ),
            ),
            "casting.prohibition.fixed_public",
        )

    kind_prohibition = _KIND_PROHIBITION.fullmatch(normalized)
    if kind_prohibition is not None:
        queries = _queries(kind_prohibition.group("subject"))
        if queries is not None:
            return (
                "static-cast-kind-prohibition-v1",
                _descriptor(
                    handler_id=STATIC_CAST_PROHIBITION_HANDLER_ID,
                    kind=StaticCastRuleKind.CAST_PROHIBITION,
                    scope=_scope(kind_prohibition.group("actor")),
                    queries=queries,
                ),
                "casting.prohibition.fixed_public",
            )

    if _ZONE_PROHIBITION.fullmatch(normalized) is not None:
        return (
            "static-cast-zone-prohibition-v1",
            _descriptor(
                handler_id=STATIC_CAST_PROHIBITION_HANDLER_ID,
                kind=StaticCastRuleKind.CAST_PROHIBITION,
                scope=StaticCastPlayerScope.ALL_PLAYERS,
                queries=(_query(),),
                origin_zones=("graveyard", "library"),
            ),
            "casting.prohibition.fixed_public",
        )
    limit = _CAST_LIMIT.fullmatch(normalized)
    if limit is not None:
        subject = limit.group("subject").casefold()
        queries = _queries(subject)
        if queries is not None:
            return (
                "static-cast-once-per-turn-limit-v1",
                _descriptor(
                    handler_id=STATIC_CAST_LIMIT_HANDLER_ID,
                    kind=StaticCastRuleKind.CAST_LIMIT,
                    scope=_scope(limit.group("actor").replace("Each player", "Players")),
                    queries=queries,
                    maximum_per_turn=1,
                ),
                "casting.limit.once_per_turn_static",
            )

    if _ENCHANTED_PLAYER_CAST_LIMIT.fullmatch(normalized) is not None:
        return (
            "static-attached-player-once-per-turn-limit-v1",
            _descriptor(
                handler_id=STATIC_CAST_LIMIT_HANDLER_ID,
                kind=StaticCastRuleKind.CAST_LIMIT,
                scope=StaticCastPlayerScope.ENCHANTED_PLAYER,
                queries=(_query(),),
                maximum_per_turn=1,
            ),
            "casting.limit.once_per_turn_static",
        )

    chosen_name = _CHOSEN_NAME_PROHIBITION.fullmatch(normalized)
    if chosen_name is not None:
        return (
            "static-cast-chosen-name-prohibition-v1",
            _descriptor(
                handler_id=STATIC_CAST_PROHIBITION_HANDLER_ID,
                kind=StaticCastRuleKind.CAST_PROHIBITION,
                scope=(
                    StaticCastPlayerScope.SOURCE_OPPONENTS
                    if chosen_name.group("opponents")
                    else StaticCastPlayerScope.ALL_PLAYERS
                ),
                queries=(_query(),),
                chosen_name=True,
            ),
            "casting.prohibition.fixed_public",
        )

    return None


def _uncounterable_rule(
    normalized: str,
) -> StaticCastRuleTemplate | None:

    if _SPELLS_AND_ABILITIES_UNCOUNTERABLE.fullmatch(normalized) is not None:
        return (
            "static-spells-abilities-uncounterable-v1",
            _descriptor(
                handler_id=STATIC_UNCOUNTERABLE_HANDLER_ID,
                kind=StaticCastRuleKind.UNCOUNTERABLE,
                scope=StaticCastPlayerScope.ALL_PLAYERS,
                queries=(_query(),),
                affects_abilities=True,
            ),
            "stack.counter.prohibition.fixed_query_static",
        )
    uncounterable = _UNCOUNTERABLE.fullmatch(normalized)
    if uncounterable is not None:
        queries = _queries(uncounterable.group("subject"))
        if queries is not None:
            return (
                "static-spell-query-uncounterable-v1",
                _descriptor(
                    handler_id=STATIC_UNCOUNTERABLE_HANDLER_ID,
                    kind=StaticCastRuleKind.UNCOUNTERABLE,
                    scope=(
                        StaticCastPlayerScope.SOURCE_CONTROLLER
                        if uncounterable.group("relation")
                        else StaticCastPlayerScope.ALL_PLAYERS
                    ),
                    queries=queries,
                ),
                "stack.counter.prohibition.fixed_query_static",
            )
    return None


def static_cast_rule_handler(
    text: str,
    *,
    source_name: str,
) -> StaticCastRuleTemplate | None:
    """Lower one closed static cast-timing, prohibition, or counter rule."""

    normalized = _without_exact_reminder(text)
    return (
        _timing_rule(normalized, source_name=source_name)
        or _cast_prohibition_or_limit_rule(normalized)
        or _uncounterable_rule(normalized)
    )


__all__ = ["StaticCastRuleTemplate", "static_cast_rule_handler"]
