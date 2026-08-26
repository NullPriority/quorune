from __future__ import annotations

"""Closed target-threaded sequences of fixed resolution effects."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..keyword_counters import keyword_counter_mechanic
from ..zone_object_keyword_model import ZONE_OBJECT_KEYWORDS
from .counter_placement_templates import (
    existing_target_counter_placement_effect_template,
    fixed_counter_placement_effect_template,
)
from .direct_target import (
    DirectPermanentTargetSpec,
    direct_permanent_target_spec,
)


_TARGET_CREATURE = re.compile(
    r"(?P<subject>target creature"
    r"(?P<relation> you control| an opponent controls| you don't control)?|it) "
    r"(?P<body>.+?) until end of turn\.?",
    re.IGNORECASE,
)
_TARGET_COMBAT_CREATURE = re.compile(
    r"(?P<subject>target (?:attacking or blocking|attacking|blocking) creature"
    r"(?: you control| an opponent controls| you don't control)?) "
    r"(?P<body>.+?) until end of turn\.?",
    re.IGNORECASE,
)
_GETS = re.compile(
    r"gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+)"
    r"(?: and gains (?P<keywords>.+))?",
    re.IGNORECASE,
)
_GAINS = re.compile(r"gains (?P<keywords>.+)", re.IGNORECASE)
FIXED_TARGET_CHARACTERISTIC_KEYWORDS = frozenset(
    {
        "deathtouch",
        "double strike",
        "first strike",
        "flying",
        "haste",
        "hexproof",
        "indestructible",
        "lifelink",
        "menace",
        "reach",
        "shroud",
        "trample",
        "vigilance",
    }
)
_SEQUENCE_MECHANIC = "fixed-target-effect-sequence"
_ZONE_OBJECT_SEQUENCE = re.compile(
    r"(?P<counter>put .+?\.) it gains (?P<keyword>[a-z ]+)\."
    r"(?: \(this effect lasts indefinitely\.\))?",
    re.IGNORECASE,
)


def _keyword_list(text: str) -> tuple[str, ...] | None:
    values = tuple(
        value.strip().casefold()
        for value in re.split(r"\s+and\s+", text)
        if value.strip()
    )
    if (
        not values
        or len(values) > 2
        or len(set(values)) != len(values)
        or any(value not in FIXED_TARGET_CHARACTERISTIC_KEYWORDS for value in values)
    ):
        return None
    return tuple(value.title() for value in values)


@dataclass(frozen=True, slots=True)
class FixedTargetCharacteristicsTemplate:
    power: int | None
    toughness: int | None
    keywords: tuple[str, ...]
    controller_relation: str | None
    target_spec: DirectPermanentTargetSpec | None = None

    def __post_init__(self) -> None:
        if (self.power is None) is not (self.toughness is None):
            raise ValueError("Power and toughness changes must be paired")
        if self.power == 0 and self.toughness == 0:
            raise ValueError("Characteristic change cannot be empty")
        if self.power is None and not self.keywords:
            raise ValueError("Characteristic change cannot be empty")
        if self.controller_relation not in {None, "any", "you", "opponent"}:
            raise ValueError("Target controller relation is unsupported")
        if self.target_spec is not None and (
            not isinstance(self.target_spec, DirectPermanentTargetSpec)
            or self.target_spec.combat_state is None
            or self.controller_relation is None
        ):
            raise ValueError(
                "Fixed characteristics direct target requires combat-state grammar"
            )
        if len(set(self.keywords)) != len(self.keywords) or any(
            value.casefold() not in FIXED_TARGET_CHARACTERISTIC_KEYWORDS
            for value in self.keywords
        ):
            raise ValueError("Granted keyword set is unsupported")

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        effects: list[Mapping[str, Any]] = []
        if self.power is not None and self.toughness is not None:
            effects.append(
                {
                    "op": "modify_stats_until_end_of_turn",
                    "card": "$target.0",
                    "power": self.power,
                    "toughness": self.toughness,
                }
            )
        effects.extend(
            {
                "op": "grant_keyword_until_end_of_turn",
                "card": "$target.0",
                "keyword": keyword,
            }
            for keyword in self.keywords
        )
        return tuple(effects)

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if self.controller_relation is None:
            return None
        if self.target_spec is not None:
            return self.target_spec.to_target_schema()
        schema: dict[str, Any] = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "types_any": ["creature"],
            "count": 1,
        }
        if self.controller_relation != "any":
            schema["controller_relation"] = self.controller_relation
        return schema

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        # A granted keyword always carries its gameplay mechanic dependency.
        # Keyword-counter vocabulary is narrower (CR 122.1b) and must not be
        # used to decide whether an independently granted ability is live,
        # but its canonical IDs remain authoritative for shared keywords.
        keyword_mechanics = tuple(
            keyword_counter_mechanic(keyword) or keyword.casefold()
            for keyword in self.keywords
        )
        return (
            "fixed-target-characteristics-until-end-of-turn-v1",
            self.effects,
            self.target_schema,
            (
                ("cr-611-continuous-effects", *keyword_mechanics)
                if self.controller_relation is None
                else (
                    "cr-611-continuous-effects",
                    "cr-115-targets",
                    *keyword_mechanics,
                )
            ),
        )


def fixed_target_characteristics_effect_template(
    text: str,
    *,
    existing_target: bool = False,
) -> FixedTargetCharacteristicsTemplate | None:
    """Parse one fixed target or target-pronoun characteristic instruction."""

    match = _TARGET_COMBAT_CREATURE.fullmatch(text.strip())
    target_spec = None
    if match is not None:
        target_spec = direct_permanent_target_spec(match.group("subject"))
        if target_spec is None:
            return None
    else:
        match = _TARGET_CREATURE.fullmatch(text.strip())
    if match is None:
        return None
    subject = match.group("subject").casefold()
    if (subject == "it") is not existing_target:
        return None
    relation = (
        ""
        if target_spec is not None
        else (match.group("relation") or "").casefold()
    )
    controller_relation = (
        None
        if existing_target
        else "you"
        if relation == " you control"
        else "opponent"
        if relation
        else "any"
    )
    if target_spec is not None:
        controller_relation = target_spec.controller_relation
    body = match.group("body")
    gets = _GETS.fullmatch(body)
    gains = _GAINS.fullmatch(body)
    if gets is not None:
        keywords = (
            _keyword_list(gets.group("keywords"))
            if gets.group("keywords")
            else ()
        )
        if keywords is None:
            return None
        return FixedTargetCharacteristicsTemplate(
            power=int(gets.group("power")),
            toughness=int(gets.group("toughness")),
            keywords=keywords,
            controller_relation=controller_relation,
            target_spec=target_spec,
        )
    if gains is None:
        return None
    keywords = _keyword_list(gains.group("keywords"))
    if keywords is None:
        return None
    return FixedTargetCharacteristicsTemplate(
        power=None,
        toughness=None,
        keywords=keywords,
        controller_relation=controller_relation,
        target_spec=target_spec,
    )


def _sentences(text: str) -> tuple[str, ...]:
    normalized = " ".join(text.strip().split())
    if any(value in normalized for value in ('"', "(", ")")):
        return ()
    clauses = tuple(
        value.strip() + "."
        for value in re.split(r"\.\s+", normalized.rstrip("."))
        if value.strip()
    )
    return clauses if 2 <= len(clauses) <= 3 else ()


def _fixed_direct_target_schema(value: Mapping[str, Any] | None) -> bool:
    try:
        DirectPermanentTargetSpec.from_target_schema(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return True


@dataclass(frozen=True, slots=True)
class FixedTargetEffectSequenceTemplate:
    effects: tuple[Mapping[str, Any], ...]
    target_schema: Mapping[str, Any]
    mechanic_ids: tuple[str, ...]

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        return (
            "fixed-target-counter-characteristics-sequence-v1",
            self.effects,
            self.target_schema,
            self.mechanic_ids,
        )


@dataclass(frozen=True, slots=True)
class FixedTargetZoneObjectKeywordSequenceTemplate:
    counter_effect: Mapping[str, Any]
    target_schema: Mapping[str, Any]
    keyword: str

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            self.counter_effect,
            {
                "op": "grant_zone_object_keyword",
                "card": "$target.0",
                "keyword": self.keyword,
            },
        )

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        keyword_mechanic = keyword_counter_mechanic(self.keyword)
        return (
            "fixed-target-counter-zone-object-keyword-sequence-v1",
            self.effects,
            self.target_schema,
            (
                _SEQUENCE_MECHANIC,
                "cr-115-targets",
                "cr-122-counters",
                "cr-611-continuous-effects",
                *((keyword_mechanic,) if keyword_mechanic else ()),
            ),
        )


def fixed_target_zone_object_keyword_sequence_template(
    text: str,
    *,
    card_name: str,
) -> FixedTargetZoneObjectKeywordSequenceTemplate | None:
    """Lower one counter placement followed by an indefinite keyword grant."""

    normalized = " ".join(text.strip().split())
    match = _ZONE_OBJECT_SEQUENCE.fullmatch(normalized)
    if match is None:
        return None
    keyword = " ".join(match.group("keyword").casefold().split())
    if keyword not in ZONE_OBJECT_KEYWORDS:
        return None
    counter = fixed_counter_placement_effect_template(
        match.group("counter"),
        card_name=card_name,
    )
    if (
        counter is None
        or counter.target_schema is None
        or counter.effects[0].get("card") != "$target.0"
    ):
        return None
    return FixedTargetZoneObjectKeywordSequenceTemplate(
        counter_effect=counter.effects[0],
        target_schema=counter.target_schema,
        keyword=keyword.title(),
    )


def fixed_target_effect_sequence_template(
    text: str,
    *,
    card_name: str,
) -> FixedTargetEffectSequenceTemplate | None:
    """Lower two or three mandatory instructions sharing target index zero."""

    clauses = _sentences(text)
    if not clauses:
        return None
    effects: list[Mapping[str, Any]] = []
    mechanic_ids: list[str] = [
        _SEQUENCE_MECHANIC,
        "cr-115-targets",
        "cr-122-counters",
        "cr-611-continuous-effects",
    ]
    target_schema: Mapping[str, Any] | None = None
    for clause in clauses:
        compiled = fixed_target_characteristics_effect_template(
            clause,
            existing_target=target_schema is not None,
        )
        if compiled is None and target_schema is None:
            compiled = fixed_counter_placement_effect_template(
                clause,
                card_name=card_name,
            )
        if compiled is None and target_schema is not None:
            compiled = existing_target_counter_placement_effect_template(clause)
        if compiled is None:
            return None
        _template_id, clause_effects, clause_schema, mechanics = compiled.compiled()
        if clause_schema is not None:
            if target_schema is not None or not _fixed_direct_target_schema(
                clause_schema
            ):
                return None
            target_schema = clause_schema
        effects.extend(clause_effects)
        mechanic_ids.extend(mechanics)
    operations = {str(effect.get("op") or "") for effect in effects}
    if target_schema is None or not {
        "place_counters",
    }.issubset(operations) or not operations.intersection(
        {"modify_stats_until_end_of_turn", "grant_keyword_until_end_of_turn"}
    ):
        return None
    return FixedTargetEffectSequenceTemplate(
        effects=tuple(effects),
        target_schema=target_schema,
        mechanic_ids=tuple(dict.fromkeys(mechanic_ids)),
    )


__all__ = [
    "FIXED_TARGET_CHARACTERISTIC_KEYWORDS",
    "FixedTargetCharacteristicsTemplate",
    "FixedTargetEffectSequenceTemplate",
    "FixedTargetZoneObjectKeywordSequenceTemplate",
    "fixed_target_characteristics_effect_template",
    "fixed_target_effect_sequence_template",
    "fixed_target_zone_object_keyword_sequence_template",
]
