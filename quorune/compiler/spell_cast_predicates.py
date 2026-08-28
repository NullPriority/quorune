from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping

from ..creature_subtypes import canonical_creature_subtype


_CONTROLLER_SPELL_CAST_TRIGGER = re.compile(
    r"^Whenever you cast (?P<quality>a noncreature|an instant or sorcery) "
    r"spell, (?P<body>.+)$",
    re.IGNORECASE,
)
_SPELL_CAST_TRIGGER = re.compile(
    r"^Whenever (?P<relation>you cast|an opponent casts|a player casts) "
    r"(?P<quality>a spell|a creature spell|an artifact spell|"
    r"an enchantment spell|an instant spell|a sorcery spell|"
    r"an instant or sorcery spell|a noncreature spell), "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_CHARACTERISTIC_SPELL_CAST_TRIGGER = re.compile(
    r"^Whenever (?P<relation>you cast|an opponent casts|a player casts) "
    r"(?P<article>a|an) (?P<quality>[A-Za-z][A-Za-z' -]*?) spell, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_SPELL_CAST_CARD_TYPES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "instant",
        "kindred",
        "planeswalker",
        "sorcery",
    }
)
_SPELL_CAST_COLORS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
_SPELL_CAST_SUPERTYPES = frozenset({"legendary", "snow"})
_SPELL_CAST_NONCREATURE_SUBTYPES = frozenset(
    {"adventure", "arcane", "aura", "equipment", "lesson", "saga", "vehicle"}
)


class FixedSpellCastController(str, Enum):
    """Closed caster relations over one normalized spell-cast event."""

    SOURCE = "source_controller"
    OPPONENT = "opponent"
    ANY = "any"


class FixedSpellCastQuality(str, Enum):
    """Closed card-type predicates already present in cast-event facts."""

    ANY = "any_spell"
    NONCREATURE = "noncreature"
    INSTANT_OR_SORCERY = "instant_or_sorcery"
    CREATURE = "creature"
    ARTIFACT = "artifact"
    ENCHANTMENT = "enchantment"
    INSTANT = "instant"
    SORCERY = "sorcery"
    PERMANENT = "permanent"


class FixedSpellCastOrigin(str, Enum):
    """Closed public origin predicates over one committed cast."""

    EXILE = "exile"
    GRAVEYARD = "graveyard"
    NOT_HAND = "not_hand"


class FixedSpellCastTurnRelation(str, Enum):
    """Whose turn the committed spell was cast during."""

    SOURCE = "source_controller"
    OPPONENT = "opponent"


class FixedSpellCastCharacteristicKind(str, Enum):
    """Closed immutable characteristic fields accepted by cast predicates."""

    TYPE = "types"
    SUBTYPE = "subtypes"
    SUPERTYPE = "supertypes"
    COLOR = "colors"
    COLORLESS = "colorless"
    MULTICOLORED = "multicolored"


@dataclass(frozen=True, slots=True)
class FixedSpellCastCharacteristicTerm:
    """One closed leaf in a static spell-characteristic disjunction."""

    kind: FixedSpellCastCharacteristicKind
    value: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FixedSpellCastCharacteristicKind):
            raise ValueError("Spell-cast characteristic terms require a closed kind")
        cardinality = self.kind in {
            FixedSpellCastCharacteristicKind.COLORLESS,
            FixedSpellCastCharacteristicKind.MULTICOLORED,
        }
        if cardinality != (self.value is None):
            raise ValueError(
                "Spell-cast characteristic terms require exactly one typed value"
            )
        if self.value is not None:
            if type(self.value) is not str or not self.value.strip():
                raise ValueError(
                    "Spell-cast characteristic values must be nonempty strings"
                )
            normalized = (
                self.value.strip().upper()
                if self.kind is FixedSpellCastCharacteristicKind.COLOR
                else self.value.strip().casefold()
            )
            object.__setattr__(self, "value", normalized)

    @property
    def variant(self) -> str:
        return (
            self.kind.value
            if self.value is None
            else f"{self.kind.value}-{self.value.casefold()}"
        )

    @property
    def event_condition(self) -> Mapping[str, Any]:
        if self.kind is FixedSpellCastCharacteristicKind.COLORLESS:
            return {"field": "colors", "op": "falsy", "value": True}
        if self.kind is FixedSpellCastCharacteristicKind.MULTICOLORED:
            return {"field": "colors", "op": "count_gte", "value": 2}
        assert self.value is not None
        return {
            "field": self.kind.value,
            "op": "contains_any",
            "value": [self.value],
        }


@dataclass(frozen=True, slots=True)
class FixedSpellCastCharacteristicQuery:
    """One to three static spell-characteristic alternatives."""

    terms_any: tuple[FixedSpellCastCharacteristicTerm, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.terms_any, tuple)
            or not 1 <= len(self.terms_any) <= 3
            or any(
                not isinstance(term, FixedSpellCastCharacteristicTerm)
                for term in self.terms_any
            )
        ):
            raise ValueError(
                "Spell-cast characteristic queries require one to three typed terms"
            )
        canonical = tuple(
            sorted(set(self.terms_any), key=lambda term: term.variant)
        )
        if len(canonical) != len(self.terms_any):
            raise ValueError("Spell-cast characteristic terms must be distinct")
        object.__setattr__(self, "terms_any", canonical)

    @property
    def variant(self) -> str:
        return "characteristic:" + ":or:".join(
            term.variant for term in self.terms_any
        )

    @property
    def event_condition(self) -> Mapping[str, Any]:
        conditions = [term.event_condition for term in self.terms_any]
        return conditions[0] if len(conditions) == 1 else {"any": conditions}


@dataclass(frozen=True, slots=True)
class FixedSpellCastSubject:
    """Immutable public predicate for one committed spell cast."""

    controller: FixedSpellCastController
    quality: FixedSpellCastQuality | None = None
    characteristic_query: FixedSpellCastCharacteristicQuery | None = None
    source_spell: bool = False
    mana_value_minimum: int | None = None
    caster_spell_number: int | None = None
    caster_spell_number_minimum: int | None = None
    origin: FixedSpellCastOrigin | None = None
    turn_relation: FixedSpellCastTurnRelation | None = None
    requires_kicked: bool = False
    requires_x_cost: bool = False
    requires_adventure: bool = False
    not_owned_by_controller: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.controller, FixedSpellCastController):
            raise ValueError("Spell-cast subjects require a closed caster relation")
        if self.quality is None and self.characteristic_query is None:
            raise ValueError(
                "Spell-cast subjects require at least one closed predicate"
            )
        if self.quality is not None and not isinstance(
            self.quality, FixedSpellCastQuality
        ):
            raise ValueError("Spell-cast subjects require a closed type predicate")
        if self.characteristic_query is not None and not isinstance(
            self.characteristic_query, FixedSpellCastCharacteristicQuery
        ):
            raise ValueError(
                "Spell-cast subjects require a typed characteristic query"
            )
        for name in (
            "source_spell",
            "requires_kicked",
            "requires_x_cost",
            "requires_adventure",
            "not_owned_by_controller",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"Spell-cast {name} must be a boolean")
        if (
            self.source_spell
            and self.controller is not FixedSpellCastController.SOURCE
        ):
            raise ValueError(
                "Source-spell cast predicates require the source controller"
            )
        if (
            self.not_owned_by_controller
            and self.controller is not FixedSpellCastController.SOURCE
        ):
            raise ValueError(
                "Spell ownership predicates require the source controller"
            )
        for name in (
            "mana_value_minimum",
            "caster_spell_number",
            "caster_spell_number_minimum",
        ):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(
                    f"Spell-cast {name} must be a positive integer or absent"
                )
        if (
            self.caster_spell_number is not None
            and self.caster_spell_number_minimum is not None
        ):
            raise ValueError(
                "Spell-cast ordinals require either equality or a minimum"
            )
        if self.origin is not None and not isinstance(
            self.origin, FixedSpellCastOrigin
        ):
            raise ValueError("Spell-cast origins require a closed relation")
        if self.turn_relation is not None and not isinstance(
            self.turn_relation, FixedSpellCastTurnRelation
        ):
            raise ValueError("Spell-cast turns require a closed relation")
    @property
    def variant(self) -> str:
        predicates = []
        if self.quality is not None:
            predicates.append(self.quality.value)
        if self.characteristic_query is not None:
            predicates.append(self.characteristic_query.variant)
        if self.source_spell:
            predicates.append("source_spell")
        if self.mana_value_minimum is not None:
            predicates.append(f"mana_value_gte_{self.mana_value_minimum}")
        if self.caster_spell_number is not None:
            predicates.append(f"spell_number_{self.caster_spell_number}")
        if self.caster_spell_number_minimum is not None:
            predicates.append(
                f"spell_number_gte_{self.caster_spell_number_minimum}"
            )
        if self.origin is not None:
            predicates.append(f"origin_{self.origin.value}")
        if self.turn_relation is not None:
            predicates.append(f"turn_{self.turn_relation.value}")
        if self.requires_kicked:
            predicates.append("kicked")
        if self.requires_x_cost:
            predicates.append("x_cost")
        if self.requires_adventure:
            predicates.append("adventure")
        if self.not_owned_by_controller:
            predicates.append("not_owned")
        return f"{self.controller.value}:" + ":and:".join(predicates)

    @property
    def extended(self) -> bool:
        return any(
            (
                self.source_spell,
                self.mana_value_minimum is not None,
                self.caster_spell_number is not None,
                self.caster_spell_number_minimum is not None,
                self.origin is not None,
                self.turn_relation is not None,
                self.requires_kicked,
                self.requires_x_cost,
                self.requires_adventure,
                self.not_owned_by_controller,
                self.quality is FixedSpellCastQuality.PERMANENT,
                self.quality is not None
                and self.characteristic_query is not None,
                self.characteristic_query is not None
                and len(self.characteristic_query.terms_any) == 3,
            )
        )

    @property
    def event_condition(self) -> Mapping[str, Any] | None:
        conditions = list(self._base_conditions())
        conditions.extend(self._fact_conditions())
        if not conditions:
            return None
        return conditions[0] if len(conditions) == 1 else {"all": conditions}

    def _base_conditions(self) -> tuple[Mapping[str, Any], ...]:
        conditions: list[Mapping[str, Any]] = []
        if self.controller is FixedSpellCastController.SOURCE:
            conditions.append(
                {"field": "controller", "op": "eq", "value": "$source.controller"}
            )
        elif self.controller is FixedSpellCastController.OPPONENT:
            conditions.append(
                {"field": "controller", "op": "ne", "value": "$source.controller"}
            )
        if self.quality is FixedSpellCastQuality.NONCREATURE:
            conditions.append(
                {
                    "not": {
                        "field": "types",
                        "op": "contains_any",
                        "value": ["creature"],
                    }
                }
            )
        elif self.quality is FixedSpellCastQuality.INSTANT_OR_SORCERY:
            conditions.append(
                {
                    "field": "types",
                    "op": "contains_any",
                    "value": ["instant", "sorcery"],
                }
            )
        elif self.quality is FixedSpellCastQuality.PERMANENT:
            conditions.append(
                {
                    "field": "types",
                    "op": "contains_any",
                    "value": [
                        "artifact",
                        "battle",
                        "creature",
                        "enchantment",
                        "planeswalker",
                    ],
                }
            )
        elif self.quality not in {None, FixedSpellCastQuality.ANY}:
            conditions.append(
                {
                    "field": "types",
                    "op": "contains_any",
                    "value": [self.quality.value],
                }
            )
        if self.characteristic_query is not None:
            conditions.append(self.characteristic_query.event_condition)
        return tuple(conditions)

    def _fact_conditions(self) -> tuple[Mapping[str, Any], ...]:
        conditions: list[Mapping[str, Any]] = []
        if self.source_spell:
            conditions.append(
                {"field": "card", "op": "eq", "value": "$source.ref"}
            )
        for field, value, op in (
            ("mana_value", self.mana_value_minimum, "gte"),
            ("caster_spell_number", self.caster_spell_number, "eq"),
            (
                "caster_spell_number",
                self.caster_spell_number_minimum,
                "gte",
            ),
        ):
            if value is not None:
                conditions.append({"field": field, "op": op, "value": value})
        if self.origin is FixedSpellCastOrigin.NOT_HAND:
            conditions.append({"field": "from", "op": "ne", "value": "hand"})
        elif self.origin is not None:
            conditions.append(
                {"field": "from", "op": "eq", "value": self.origin.value}
            )
        if self.turn_relation is not None:
            conditions.append(
                {
                    "field": "active_player",
                    "op": (
                        "eq"
                        if self.turn_relation is FixedSpellCastTurnRelation.SOURCE
                        else "ne"
                    ),
                    "value": "$source.controller",
                }
            )
        for field, required in (
            ("kicked", self.requires_kicked),
            ("has_x_cost", self.requires_x_cost),
            ("has_adventure", self.requires_adventure),
        ):
            if required:
                conditions.append(
                    {"field": field, "op": "truthy", "value": True}
                )
        if self.not_owned_by_controller:
            conditions.append(
                {"field": "owner", "op": "ne", "value": "$source.controller"}
            )
        return tuple(conditions)


@dataclass(frozen=True, slots=True)
class FixedSpellCastBindingSpec:
    """Parsed spell-cast subject and effect body without trigger-owner coupling."""

    subject: FixedSpellCastSubject
    body: str
    variant: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.subject, FixedSpellCastSubject):
            raise ValueError("Spell-cast binding specs require a typed subject")
        if type(self.body) is not str or not self.body:
            raise ValueError("Spell-cast binding specs require a nonempty body")
        if self.variant is not None and (
            type(self.variant) is not str or not self.variant
        ):
            raise ValueError("Spell-cast binding variants must be nonempty")

    @property
    def binding_variant(self) -> str:
        return self.variant or self.subject.variant


def _spell_cast_characteristic_term(
    value: str,
) -> FixedSpellCastCharacteristicTerm | None:
    normalized = " ".join(value.casefold().split())
    if normalized == "colorless":
        return FixedSpellCastCharacteristicTerm(
            FixedSpellCastCharacteristicKind.COLORLESS
        )
    if normalized == "multicolored":
        return FixedSpellCastCharacteristicTerm(
            FixedSpellCastCharacteristicKind.MULTICOLORED
        )
    if normalized in _SPELL_CAST_COLORS:
        return FixedSpellCastCharacteristicTerm(
            FixedSpellCastCharacteristicKind.COLOR,
            _SPELL_CAST_COLORS[normalized],
        )
    if normalized in _SPELL_CAST_CARD_TYPES:
        return FixedSpellCastCharacteristicTerm(
            FixedSpellCastCharacteristicKind.TYPE,
            normalized,
        )
    if normalized in _SPELL_CAST_SUPERTYPES:
        return FixedSpellCastCharacteristicTerm(
            FixedSpellCastCharacteristicKind.SUPERTYPE,
            normalized,
        )
    subtype = (
        normalized
        if normalized in _SPELL_CAST_NONCREATURE_SUBTYPES
        else canonical_creature_subtype(normalized)
    )
    if subtype is None:
        return None
    return FixedSpellCastCharacteristicTerm(
        FixedSpellCastCharacteristicKind.SUBTYPE,
        subtype,
    )


def _spell_cast_characteristic_query(
    quality: str,
) -> FixedSpellCastCharacteristicQuery | None:
    parts = tuple(
        " ".join(value.split()) for value in quality.casefold().split(" or ")
    )
    if not 1 <= len(parts) <= 2:
        return None
    terms = tuple(_spell_cast_characteristic_term(value) for value in parts)
    if any(term is None for term in terms):
        return None
    try:
        return FixedSpellCastCharacteristicQuery(
            tuple(term for term in terms if term is not None)
        )
    except ValueError:
        return None


def _spell_cast_controller(value: str) -> FixedSpellCastController:
    return {
        "you": FixedSpellCastController.SOURCE,
        "you cast": FixedSpellCastController.SOURCE,
        "an opponent": FixedSpellCastController.OPPONENT,
        "an opponent casts": FixedSpellCastController.OPPONENT,
        "a player": FixedSpellCastController.ANY,
        "a player casts": FixedSpellCastController.ANY,
        "another player": FixedSpellCastController.OPPONENT,
    }[" ".join(value.casefold().split())]


def _spell_cast_base_predicate(
    descriptor: str,
) -> tuple[
    FixedSpellCastQuality | None,
    FixedSpellCastCharacteristicQuery | None,
]:
    normalized = " ".join(descriptor.casefold().split())
    ordinary = {
        "spell": FixedSpellCastQuality.ANY,
        "creature spell": FixedSpellCastQuality.CREATURE,
        "artifact spell": FixedSpellCastQuality.ARTIFACT,
        "noncreature spell": FixedSpellCastQuality.NONCREATURE,
    }.get(normalized)
    if ordinary is not None:
        return ordinary, None
    if normalized == "eldrazi creature spell":
        return (
            FixedSpellCastQuality.CREATURE,
            FixedSpellCastCharacteristicQuery(
                (
                    FixedSpellCastCharacteristicTerm(
                        FixedSpellCastCharacteristicKind.SUBTYPE,
                        "eldrazi",
                    ),
                )
            ),
        )
    raise ValueError("Unsupported fixed spell-cast descriptor")


def _binding(
    *,
    body: str,
    controller: FixedSpellCastController,
    descriptor: str = "spell",
    variant: str | None = None,
    **facts: Any,
) -> FixedSpellCastBindingSpec:
    quality, query = _spell_cast_base_predicate(descriptor)
    return FixedSpellCastBindingSpec(
        subject=FixedSpellCastSubject(
            controller=controller,
            quality=quality,
            characteristic_query=query,
            **facts,
        ),
        body=body,
        variant=variant,
    )


def _basic_spell_cast_binding(
    material_line: str,
) -> FixedSpellCastBindingSpec | None:
    controller_cast = _CONTROLLER_SPELL_CAST_TRIGGER.fullmatch(material_line)
    if controller_cast is not None:
        quality = controller_cast.group("quality").casefold()
        variant = (
            "noncreature" if quality == "a noncreature" else "instant_or_sorcery"
        )
        subject = FixedSpellCastSubject(
            controller=FixedSpellCastController.SOURCE,
            quality=(
                FixedSpellCastQuality.NONCREATURE
                if quality == "a noncreature"
                else FixedSpellCastQuality.INSTANT_OR_SORCERY
            ),
        )
        return FixedSpellCastBindingSpec(
            subject=subject,
            body=controller_cast.group("body"),
            variant=variant,
        )
    ordinary = _SPELL_CAST_TRIGGER.fullmatch(material_line)
    if ordinary is not None:
        quality = {
            "a spell": FixedSpellCastQuality.ANY,
            "a noncreature spell": FixedSpellCastQuality.NONCREATURE,
            "an instant or sorcery spell": FixedSpellCastQuality.INSTANT_OR_SORCERY,
            "a creature spell": FixedSpellCastQuality.CREATURE,
            "an artifact spell": FixedSpellCastQuality.ARTIFACT,
            "an enchantment spell": FixedSpellCastQuality.ENCHANTMENT,
            "an instant spell": FixedSpellCastQuality.INSTANT,
            "a sorcery spell": FixedSpellCastQuality.SORCERY,
        }[ordinary.group("quality").casefold()]
        return FixedSpellCastBindingSpec(
            subject=FixedSpellCastSubject(
                controller=_spell_cast_controller(ordinary.group("relation")),
                quality=quality,
            ),
            body=ordinary.group("body"),
        )
    characteristic = _CHARACTERISTIC_SPELL_CAST_TRIGGER.fullmatch(material_line)
    if characteristic is None:
        return None
    quality = characteristic.group("quality")
    expected_article = "an" if quality[0].casefold() in "aeiou" else "a"
    query = _spell_cast_characteristic_query(quality)
    if query is None or characteristic.group("article").casefold() != expected_article:
        return None
    return FixedSpellCastBindingSpec(
        subject=FixedSpellCastSubject(
            controller=_spell_cast_controller(characteristic.group("relation")),
            characteristic_query=query,
        ),
        body=characteristic.group("body"),
    )


def _history_spell_cast_binding(
    material_line: str,
) -> FixedSpellCastBindingSpec | None:
    source_spell = re.fullmatch(
        r"When you cast this spell, (?P<body>.+)", material_line, re.IGNORECASE
    )
    if source_spell is not None:
        return _binding(
            body=source_spell.group("body"),
            controller=FixedSpellCastController.SOURCE,
            source_spell=True,
        )
    mana_value = re.fullmatch(
        r"Whenever you cast (?:a|an) (?P<descriptor>spell|creature spell|"
        r"artifact spell|noncreature spell|Eldrazi creature spell) with mana "
        r"value (?P<amount>[1-9][0-9]*) or greater, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if mana_value is not None:
        return _binding(
            body=mana_value.group("body"),
            controller=FixedSpellCastController.SOURCE,
            descriptor=mana_value.group("descriptor"),
            mana_value_minimum=int(mana_value.group("amount")),
        )
    ordinal = re.fullmatch(
        r"Whenever you cast your (?P<ordinal>first|second) spell each turn, "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if ordinal is not None:
        return _binding(
            body=ordinal.group("body"),
            controller=FixedSpellCastController.SOURCE,
            caster_spell_number={"first": 1, "second": 2}[
                ordinal.group("ordinal").casefold()
            ],
        )
    first_opponent_turn = re.fullmatch(
        r"Whenever you cast your first spell during each opponent's turn, "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if first_opponent_turn is not None:
        return _binding(
            body=first_opponent_turn.group("body"),
            controller=FixedSpellCastController.SOURCE,
            caster_spell_number=1,
            turn_relation=FixedSpellCastTurnRelation.OPPONENT,
        )
    player_ordinal = re.fullmatch(
        r"Whenever (?P<controller>a player|an opponent) casts their second "
        r"spell each turn, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if player_ordinal is not None:
        return _binding(
            body=player_ordinal.group("body"),
            controller=_spell_cast_controller(player_ordinal.group("controller")),
            caster_spell_number=2,
        )
    return _turn_spell_cast_binding(material_line)


def _turn_spell_cast_binding(
    material_line: str,
) -> FixedSpellCastBindingSpec | None:
    turn_relative = re.fullmatch(
        r"Whenever you cast (?:a )?(?P<descriptor>spell|creature spell) "
        r"during (?P<turn>an opponent's|your) turn, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if turn_relative is not None:
        return _binding(
            body=turn_relative.group("body"),
            controller=FixedSpellCastController.SOURCE,
            descriptor=turn_relative.group("descriptor"),
            turn_relation=(
                FixedSpellCastTurnRelation.SOURCE
                if turn_relative.group("turn").casefold() == "your"
                else FixedSpellCastTurnRelation.OPPONENT
            ),
        )
    opponent_during_turn = re.fullmatch(
        r"Whenever an opponent casts a spell during your turn, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if opponent_during_turn is not None:
        return _binding(
            body=opponent_during_turn.group("body"),
            controller=FixedSpellCastController.OPPONENT,
            turn_relation=FixedSpellCastTurnRelation.SOURCE,
        )
    after_first = re.fullmatch(
        r"Whenever you cast a spell (?:during your turn )?other than your "
        r"first spell (?:that turn|each turn), (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if after_first is None:
        return None
    return _binding(
        body=after_first.group("body"),
        controller=FixedSpellCastController.SOURCE,
        caster_spell_number_minimum=2,
    )


def _origin_spell_cast_binding(
    material_line: str,
) -> FixedSpellCastBindingSpec | None:
    origin = re.fullmatch(
        r"Whenever you cast a spell from (?P<origin>exile|your graveyard|"
        r"anywhere other than your hand), (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if origin is not None:
        return _binding(
            body=origin.group("body"),
            controller=FixedSpellCastController.SOURCE,
            origin={
                "exile": FixedSpellCastOrigin.EXILE,
                "your graveyard": FixedSpellCastOrigin.GRAVEYARD,
                "anywhere other than your hand": FixedSpellCastOrigin.NOT_HAND,
            }[origin.group("origin").casefold()],
        )
    other_origin = re.fullmatch(
        r"Whenever (?P<controller>an opponent|another player) casts a spell "
        r"from anywhere other than their hand, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if other_origin is not None:
        return _binding(
            body=other_origin.group("body"),
            controller=_spell_cast_controller(other_origin.group("controller")),
            origin=FixedSpellCastOrigin.NOT_HAND,
        )
    any_graveyard = re.fullmatch(
        r"Whenever a player casts a spell from a graveyard, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if any_graveyard is not None:
        return _binding(
            body=any_graveyard.group("body"),
            controller=FixedSpellCastController.ANY,
            origin=FixedSpellCastOrigin.GRAVEYARD,
        )
    return _trait_spell_cast_binding(material_line)


def _trait_spell_cast_binding(
    material_line: str,
) -> FixedSpellCastBindingSpec | None:
    historic = re.fullmatch(
        r"Whenever you cast a historic spell, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if historic is not None:
        query = FixedSpellCastCharacteristicQuery(
            tuple(
                term
                for term in (
                    _spell_cast_characteristic_term("artifact"),
                    _spell_cast_characteristic_term("legendary"),
                    _spell_cast_characteristic_term("saga"),
                )
                if term is not None
            )
        )
        return FixedSpellCastBindingSpec(
            subject=FixedSpellCastSubject(
                controller=FixedSpellCastController.SOURCE,
                characteristic_query=query,
            ),
            body=historic.group("body"),
        )
    for pattern, fact in (
        (r"Whenever you cast a kicked spell, (?P<body>.+)", "requires_kicked"),
        (
            r"Whenever you cast a spell with \{X\} in its mana cost, (?P<body>.+)",
            "requires_x_cost",
        ),
    ):
        match = re.fullmatch(pattern, material_line, re.IGNORECASE)
        if match is not None:
            return _binding(
                body=match.group("body"),
                controller=FixedSpellCastController.SOURCE,
                **{fact: True},
            )
    not_owned = re.fullmatch(
        r"Whenever you cast (?:a )?(?P<descriptor>spell|noncreature spell) "
        r"you don't own, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if not_owned is not None:
        return _binding(
            body=not_owned.group("body"),
            controller=FixedSpellCastController.SOURCE,
            descriptor=not_owned.group("descriptor"),
            not_owned_by_controller=True,
        )
    return _characteristic_trait_spell_cast_binding(material_line)


def _characteristic_trait_spell_cast_binding(
    material_line: str,
) -> FixedSpellCastBindingSpec | None:
    colored_permanent = re.fullmatch(
        r"Whenever you cast a (?P<color>white|blue|black|red|green) "
        r"permanent spell, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if colored_permanent is not None:
        color = _spell_cast_characteristic_term(colored_permanent.group("color"))
        assert color is not None
        return FixedSpellCastBindingSpec(
            subject=FixedSpellCastSubject(
                controller=FixedSpellCastController.SOURCE,
                quality=FixedSpellCastQuality.PERMANENT,
                characteristic_query=FixedSpellCastCharacteristicQuery((color,)),
            ),
            body=colored_permanent.group("body"),
        )
    adventure = re.fullmatch(
        r"Whenever you cast a creature spell that has an Adventure, "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if adventure is None:
        return None
    return FixedSpellCastBindingSpec(
        subject=FixedSpellCastSubject(
            controller=FixedSpellCastController.SOURCE,
            quality=FixedSpellCastQuality.CREATURE,
            requires_adventure=True,
        ),
        body=adventure.group("body"),
    )


def fixed_spell_cast_binding_spec(
    material_line: str,
) -> FixedSpellCastBindingSpec | None:
    """Parse one bounded predicate over immutable normalized cast facts."""

    for parser in (
        _basic_spell_cast_binding,
        _history_spell_cast_binding,
        _origin_spell_cast_binding,
    ):
        binding = parser(material_line)
        if binding is not None:
            return binding
    return None


__all__ = [
    "FixedSpellCastBindingSpec",
    "FixedSpellCastCharacteristicKind",
    "FixedSpellCastCharacteristicQuery",
    "FixedSpellCastCharacteristicTerm",
    "FixedSpellCastController",
    "FixedSpellCastOrigin",
    "FixedSpellCastQuality",
    "FixedSpellCastSubject",
    "FixedSpellCastTurnRelation",
    "fixed_spell_cast_binding_spec",
]
