from __future__ import annotations

"""Closed grammar for public static spell-cost increases and reductions."""

import re

from ..cast_cost_modifiers import (
    CastCostAffectedController,
    CastCostModifierError,
    CastCostOrdinal,
    CastCostTurnRelation,
    PublicCastCostModifierSpec,
    PublicCastCostModifierV2Spec,
)
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..self_cast_reductions import (
    CastReductionMetric,
    CastReductionMetricKind,
    CastReductionObjectQuery,
    CastReductionQueryScope,
    CastReductionTurnFact,
)
from .cast_cost_modifier_templates import fixed_spell_predicate


PublicCastCostModifierTemplate = PublicCastCostModifierSpec


_ADJUSTMENT = re.compile(
    r"^(?P<subject>.+?) cost(?:s)? \{(?P<amount>[1-9][0-9]*)\} "
    r"(?P<direction>less|more) to cast\.?$",
    re.IGNORECASE,
)
_COLORED_ADJUSTMENT = re.compile(
    r"^(?P<subject>.+?) cost(?:s)? (?P<amount>(?:\{[WUBRG]\})+) "
    r"(?P<direction>less|more) to cast(?:\. This effect reduces only the "
    r"amount of colored mana you pay(?:\. \(.+\))?)?\.?$",
    re.IGNORECASE,
)
_DYNAMIC_ADJUSTMENT = re.compile(
    r"^(?P<subject>.+?) cost(?:s)? \{(?P<amount>[1-9][0-9]*)\} less "
    r"to cast for each (?P<metric>.+?)\.?$",
    re.IGNORECASE,
)
_AFFINITY_GRANT = re.compile(
    r"^(?P<subject>Artifact creature spells you cast|Spells you cast) have "
    r"affinity for artifacts\.?(?: \(.+\))?$",
    re.IGNORECASE,
)
_VARIABLE_PUBLIC_ADJUSTMENT = re.compile(
    r"^(?P<subject>.+?) cost(?:s)? \{X\} less to cast, where X is the "
    r"amount of life you gained this turn\.?$",
    re.IGNORECASE,
)
_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}


def _historic_predicates() -> tuple[ObjectQuerySpec, ...]:
    return (
        ObjectQuerySpec(types_all=("artifact",)),
        ObjectQuerySpec(supertypes_all=("legendary",)),
        ObjectQuerySpec(subtypes_all=("saga",)),
    )


def _spell_predicates(subject: str) -> tuple[ObjectQuerySpec, ...] | None:
    normalized = " ".join(subject.split())
    if normalized.casefold() == "historic spells you cast":
        return _historic_predicates()
    if normalized.casefold() == "creature spells with flying you cast":
        return (
            ObjectQuerySpec(types_all=("creature",), keywords_all=("flying",)),
        )
    if normalized.casefold() == "spells with flash you cast":
        return (ObjectQuerySpec(keywords_all=("flash",)),)
    paired = re.fullmatch(
        r"(?P<first>white|blue|black|red|green) "
        r"(?P<kind>creature|enchantment) spells and "
        r"(?P<second>white|blue|black|red|green) (?P=kind) spells"
        r"(?: you cast)?",
        normalized,
        re.IGNORECASE,
    )
    if paired is not None:
        kind = paired.group("kind").casefold()
        return tuple(
            ObjectQuerySpec(
                types_all=(kind,),
                colors_all=(_COLORS[paired.group(name).casefold()],),
            )
            for name in ("first", "second")
        )
    bounded = re.fullmatch(
        r"(?P<base>.+? spells you cast) with mana value "
        r"(?P<minimum>[1-9][0-9]*) or greater",
        normalized,
        re.IGNORECASE,
    )
    if bounded is not None:
        predicate = fixed_spell_predicate(bounded.group("base"))
        return (predicate,) if predicate is not None else None
    predicate = fixed_spell_predicate(normalized)
    return (predicate,) if predicate is not None else None


def _mana_adjustment(token: str, *, direction: str) -> tuple[tuple[str, int], ...]:
    sign = -1 if direction.casefold() == "less" else 1
    amounts: dict[str, int] = {}
    for symbol in re.findall(r"\{([WUBRG])\}", token.upper()):
        amounts[symbol] = amounts.get(symbol, 0) + sign
    return tuple(sorted(amounts.items()))


def _dynamic_metric(text: str) -> CastReductionMetric | None:
    normalized = " ".join(text.casefold().split())
    counter = re.fullmatch(
        r"(?P<name>\+1/\+1|oil) counter on (?:this creature|this artifact|.+)",
        normalized,
    )
    if counter is not None:
        return CastReductionMetric(
            kind=CastReductionMetricKind.SOURCE_COUNTER_COUNT,
            counter_name=counter.group("name"),
            schema_version=2,
        )
    if normalized == "creature token you control":
        query = ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            token=True,
        )
        return CastReductionMetric(
            kind=CastReductionMetricKind.OBJECT_COUNT,
            queries=(
                CastReductionObjectQuery(
                    CastReductionQueryScope.CONTROLLER_ZONE,
                    query,
                ),
            ),
            schema_version=2,
        )
    if normalized == "creature you control with a +1/+1 counter on it":
        query = ObjectQuerySpec(
            zones=("battlefield",),
            types_all=("creature",),
            state_predicate=PermanentStatePredicateSpec(
                counter_name="+1/+1",
                minimum_counter_count=1,
            ),
        )
        return CastReductionMetric(
            kind=CastReductionMetricKind.OBJECT_COUNT,
            queries=(
                CastReductionObjectQuery(
                    CastReductionQueryScope.CONTROLLER_ZONE,
                    query,
                ),
            ),
            schema_version=2,
        )
    fact = {
        "time you've cast a commander from the command zone this game": (
            CastReductionTurnFact.CONTROLLER_COMMANDER_CAST_COUNT
        ),
        "1 life your opponents have lost this turn": (
            CastReductionTurnFact.OPPONENT_LIFE_LOST_AMOUNT
        ),
    }.get(normalized)
    if fact is not None:
        return CastReductionMetric(
            kind=CastReductionMetricKind.TURN_VALUE,
            turn_fact=fact,
            schema_version=2,
        )
    return None


def _subject_spec(
    subject: str,
) -> tuple[
    CastCostAffectedController,
    tuple[ObjectQuerySpec, ...],
    tuple[str, ...],
    tuple[str, ...],
    CastCostTurnRelation,
    CastCostOrdinal,
] | None:
    normalized = " ".join(subject.split())
    turn_relation = CastCostTurnRelation.ANY
    ordinal = CastCostOrdinal.ANY
    origin_zones: tuple[str, ...] = ()
    excluded_origin_zones: tuple[str, ...] = ()

    for prefix, relation in (
        ("During turns other than yours, ", CastCostTurnRelation.NOT_SOURCE_CONTROLLER_TURN),
        ("During your turn, ", CastCostTurnRelation.SOURCE_CONTROLLER_TURN),
    ):
        if normalized.casefold().startswith(prefix.casefold()):
            normalized = normalized[len(prefix) :]
            turn_relation = relation
            break

    ordinal_match = re.fullmatch(
        r"The (?P<ordinal>first|second) (?P<quality>.+?) spell you cast each turn",
        normalized,
        re.IGNORECASE,
    )
    if ordinal_match is not None:
        ordinal = CastCostOrdinal(ordinal_match.group("ordinal").casefold())
        quality = ordinal_match.group("quality")
        normalized = (
            "Spells you cast"
            if quality.casefold() == "spell"
            else f"{quality} spells you cast"
        )

    origin_match = re.fullmatch(
        r"Spells you cast from (?P<origin>your graveyard|your graveyard or from exile)",
        normalized,
        re.IGNORECASE,
    )
    if origin_match is not None:
        origin = origin_match.group("origin").casefold()
        normalized = "Spells you cast"
        if origin == "your graveyard":
            origin_zones = ("graveyard",)
        elif origin == "your graveyard or from exile":
            origin_zones = ("graveyard", "exile")
        else:
            origin_zones = ("graveyard", "exile")

    relation = CastCostAffectedController.SOURCE_CONTROLLER
    predicate_subject = normalized
    lowered = normalized.casefold()
    if lowered in {"spell", "spells", "each spell"}:
        relation = CastCostAffectedController.ALL_PLAYERS
        predicate_subject = "Spells you cast"
    elif lowered == "spells your opponents cast":
        relation = CastCostAffectedController.SOURCE_OPPONENTS
        predicate_subject = "Spells you cast"
    elif lowered.endswith(" spells your opponents cast"):
        relation = CastCostAffectedController.SOURCE_OPPONENTS
        quality = normalized[: -len(" spells your opponents cast")]
        predicate_subject = (
            "Spells you cast" if not quality else f"{quality} spells you cast"
        )
    elif lowered.endswith(" spells"):
        relation = CastCostAffectedController.ALL_PLAYERS
        quality = normalized[: -len(" spells")]
        predicate_subject = (
            "Spells you cast" if not quality else f"{quality} spells you cast"
        )
    predicates = _spell_predicates(predicate_subject)
    if predicates is None:
        return None
    return (
        relation,
        predicates,
        origin_zones,
        excluded_origin_zones,
        turn_relation,
        ordinal,
    )


def public_cast_cost_modifier_template(
    text: str,
) -> PublicCastCostModifierTemplate | None:
    """Parse one fixed public total-cost adjustment and reject open predicates."""

    normalized = re.sub(
        r"^[A-Za-z][A-Za-z ]+ (?:—|�) ",
        "",
        " ".join(text.strip().split()),
    )
    match = _ADJUSTMENT.fullmatch(normalized)
    if match is None:
        return None
    subject = _subject_spec(match.group("subject"))
    if subject is None:
        return None
    (
        affected_controller,
        predicates,
        origin_zones,
        excluded_origin_zones,
        turn_relation,
        ordinal,
    ) = subject
    amount = int(match.group("amount"))
    adjustment = amount if match.group("direction").casefold() == "more" else -amount
    try:
        return PublicCastCostModifierSpec(
            affected_controller=affected_controller,
            predicates_any=predicates,
            generic_adjustment=adjustment,
            cast_origin_zones=origin_zones,
            excluded_cast_origin_zones=excluded_origin_zones,
            turn_relation=turn_relation,
            ordinal=ordinal,
        )
    except CastCostModifierError:
        return None


def public_cast_cost_modifier_v2_template(
    text: str,
) -> PublicCastCostModifierV2Spec | None:
    """Parse the bounded colored and public-value total-cost extension."""

    def build(**kwargs) -> PublicCastCostModifierV2Spec | None:
        try:
            return PublicCastCostModifierV2Spec(**kwargs)
        except CastCostModifierError:
            return None

    normalized = re.sub(
        r"^[A-Za-z][A-Za-z ]+ (?:—|�) ",
        "",
        " ".join(text.strip().split()),
    )
    affinity = _AFFINITY_GRANT.fullmatch(normalized)
    if affinity is not None:
        subject = _subject_spec(affinity.group("subject"))
        if subject is None:
            return None
        query = CastReductionObjectQuery(
            CastReductionQueryScope.CONTROLLER_ZONE,
            ObjectQuerySpec(zones=("battlefield",), types_all=("artifact",)),
        )
        return build(
            affected_controller=subject[0],
            predicates_any=subject[1],
            mana_adjustment=(("GENERIC", -1),),
            multiplier=CastReductionMetric(
                kind=CastReductionMetricKind.OBJECT_COUNT,
                queries=(query,),
                schema_version=2,
            ).to_dict(),
        )

    variable = _VARIABLE_PUBLIC_ADJUSTMENT.fullmatch(normalized)
    if variable is not None:
        subject = _subject_spec(variable.group("subject"))
        if subject is None:
            return None
        return build(
            affected_controller=subject[0],
            predicates_any=subject[1],
            mana_adjustment=(("GENERIC", -1),),
            multiplier=CastReductionMetric(
                kind=CastReductionMetricKind.TURN_VALUE,
                turn_fact=CastReductionTurnFact.CONTROLLER_LIFE_GAINED_AMOUNT,
                schema_version=2,
            ).to_dict(),
        )

    dynamic = _DYNAMIC_ADJUSTMENT.fullmatch(normalized)
    if dynamic is not None:
        subject = _subject_spec(dynamic.group("subject"))
        metric = _dynamic_metric(dynamic.group("metric"))
        if subject is None or metric is None:
            return None
        return build(
            affected_controller=subject[0],
            predicates_any=subject[1],
            mana_adjustment=(("GENERIC", -int(dynamic.group("amount"))),),
            multiplier=metric.to_dict(),
        )

    colored = _COLORED_ADJUSTMENT.fullmatch(normalized)
    if colored is not None:
        subject = _subject_spec(colored.group("subject"))
        adjustment = _mana_adjustment(
            colored.group("amount"),
            direction=colored.group("direction"),
        )
        if subject is None or not adjustment:
            return None
        return build(
            affected_controller=subject[0],
            predicates_any=subject[1],
            mana_adjustment=adjustment,
            cast_origin_zones=subject[2],
            excluded_cast_origin_zones=subject[3],
            turn_relation=subject[4],
            ordinal=subject[5],
        )

    defense_grid = re.fullmatch(
        r"Each spell costs \{(?P<amount>[1-9][0-9]*)\} more to cast except "
        r"during its controller's turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if defense_grid is not None:
        return build(
            affected_controller=CastCostAffectedController.ALL_PLAYERS,
            predicates_any=(ObjectQuerySpec(),),
            mana_adjustment=(
                ("GENERIC", int(defense_grid.group("amount"))),
            ),
            turn_relation=CastCostTurnRelation.NOT_CASTER_TURN,
        )

    fixed = _ADJUSTMENT.fullmatch(normalized)
    if fixed is None:
        return None
    raw_subject = fixed.group("subject")
    minimum_mana_value = None
    bounded = re.fullmatch(
        r"(?P<base>.+? spells you cast) with mana value "
        r"(?P<minimum>[1-9][0-9]*) or greater",
        raw_subject,
        re.IGNORECASE,
    )
    if bounded is not None:
        raw_subject = bounded.group("base")
        minimum_mana_value = int(bounded.group("minimum"))
    excluded_origins: tuple[str, ...] = ()
    if raw_subject.casefold() == "spells you cast from anywhere other than your hand":
        raw_subject = "Spells you cast"
        excluded_origins = ("hand",)
    subject = _subject_spec(raw_subject)
    if subject is None:
        return None
    amount = int(fixed.group("amount"))
    sign = 1 if fixed.group("direction").casefold() == "more" else -1
    return build(
        affected_controller=subject[0],
        predicates_any=subject[1],
        mana_adjustment=(("GENERIC", sign * amount),),
        cast_origin_zones=subject[2],
        excluded_cast_origin_zones=excluded_origins or subject[3],
        turn_relation=subject[4],
        ordinal=subject[5],
        minimum_mana_value=minimum_mana_value,
    )


__all__ = [
    "CastCostAffectedController",
    "CastCostOrdinal",
    "CastCostTurnRelation",
    "PublicCastCostModifierTemplate",
    "public_cast_cost_modifier_template",
    "public_cast_cost_modifier_v2_template",
]
