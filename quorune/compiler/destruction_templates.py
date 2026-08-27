from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from ..affected_permanents import (
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
)
from ..mana import BASIC_LAND_MANA
from ..object_predicate import ObjectQuerySpec
from .direct_target import (
    DirectPermanentTargetSpec,
    compiled_direct_target,
    direct_permanent_target_spec,
    direct_target_effect,
)


@dataclass(frozen=True, slots=True)
class TargetedDestructionEffectTemplate:
    """Closed lowering for one mandatory direct-target destruction."""

    target_spec: DirectPermanentTargetSpec
    regeneration_prohibited: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.target_spec, DirectPermanentTargetSpec):
            raise ValueError("Destruction target predicate is unsupported")
        if type(self.regeneration_prohibited) is not bool:
            raise ValueError(
                "Destruction regeneration prohibition must be boolean"
            )

    @property
    def template_id(self) -> str:
        return (
            f"destroy-target-{self.target_spec.slug}-regeneration-prohibited-v1"
            if self.regeneration_prohibited
            else f"destroy-target-{self.target_spec.slug}-v2"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        effect = dict(
            direct_target_effect("destroy", reference_field="card")[0]
        )
        if self.regeneration_prohibited:
            effect["regeneration_prohibited"] = True
        return (effect,)

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return self.target_spec.to_target_schema()

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            "destroy",
            "cr-115-targets",
            *(("regeneration-prohibition",) if self.regeneration_prohibited else ()),
        )

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        return compiled_direct_target(
            template_id=self.template_id,
            effects=self.effects,
            target_schema=self.target_schema,
            mechanics=self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class MassDestructionEffectTemplate:
    """Closed lowering for one simultaneous fixed battlefield set."""

    spec: AffectedPermanentSetSpec
    target_schema: Mapping[str, Any] | None = None
    regeneration_prohibited: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.spec, AffectedPermanentSetSpec):
            raise ValueError("Mass destruction requires a typed affected set")
        if type(self.regeneration_prohibited) is not bool:
            raise ValueError(
                "Mass-destruction regeneration prohibition must be boolean"
            )
        schema = self.target_schema
        if schema is not None:
            schema = dict(schema)
            expected = _player_target_schema(
                opponent=schema.get("player_relation") == "opponent"
            )
            if schema != expected:
                raise ValueError("Mass destruction player target is unsupported")
        needs_target = (
            self.spec.controller_relation
            is PermanentControllerRelation.TARGET_PLAYER
        )
        if needs_target is not (schema is not None):
            raise ValueError(
                "Mass destruction target schema contradicts its affected set"
            )
        object.__setattr__(self, "target_schema", schema)

    @property
    def template_id(self) -> str:
        return (
            "destroy-fixed-set-"
            f"{self.spec.fingerprint[:16]}-regeneration-prohibited-v1"
            if self.regeneration_prohibited
            else f"destroy-fixed-set-{self.spec.fingerprint[:16]}-v1"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        effect = {
            "op": "destroy_all",
            "source": "$source",
            "set": self.spec.to_dict(),
        }
        if self.regeneration_prohibited:
            effect["regeneration_prohibited"] = True
        return (effect,)

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            "destroy",
            "destroy-fixed-set",
            *(
                ("cr-115-targets",)
                if self.target_schema is not None
                else ()
            ),
            *(("regeneration-prohibition",) if self.regeneration_prohibited else ()),
        )

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


_MASS_DESTRUCTION_CARD_TYPES = (
    "artifact",
    "battle",
    "creature",
    "enchantment",
    "land",
    "planeswalker",
)
_TYPE_WORDS = {
    f"{card_type}s": card_type
    for card_type in _MASS_DESTRUCTION_CARD_TYPES
}
_COLOR_WORDS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
_BASIC_LAND_SUBTYPES = {
    (
        land_type if land_type.endswith("s") else f"{land_type}s"
    ): land_type
    for land_type in BASIC_LAND_MANA
}
_REGENERATION_PROHIBITION = re.compile(
    r"(?P<destruction>destroy .+?\.)\s+"
    r"(?P<subject>it|they|those creatures|a creature destroyed this way|"
    r"creatures destroyed this way) can(?:not|'t) be regenerated"
    r"(?: this turn)?\.?",
    re.IGNORECASE,
)


def _split_regeneration_prohibition(
    text: str,
) -> tuple[str, str | None] | None:
    normalized = " ".join(text.strip().split())
    match = _REGENERATION_PROHIBITION.fullmatch(normalized)
    if match is None:
        return normalized, None
    return (
        match.group("destruction"),
        match.group("subject").casefold(),
    )


def _player_target_schema(*, opponent: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
    }
    if opponent:
        result["player_relation"] = "opponent"
    else:
        result["player_relation"] = "any"
    return result


def _split_type_list(text: str) -> tuple[str, ...] | None:
    normalized = text.casefold().replace(", and ", ", ").replace(" and ", ", ")
    words = tuple(part.strip() for part in normalized.split(","))
    if not words or any(word not in _TYPE_WORDS for word in words):
        return None
    values = tuple(sorted({_TYPE_WORDS[word] for word in words}))
    return values if len(values) == len(words) else None


def fixed_affected_permanent_query(
    subject: str,
) -> tuple[ObjectQuerySpec, bool] | None:
    phrase = " ".join(subject.casefold().split())
    exclude_source = False
    if phrase.startswith("other "):
        exclude_source = True
        phrase = phrase[6:]

    kwargs: dict[str, Any] = {"zones": ("battlefield",)}
    if phrase in {"permanents", "nonland permanents"}:
        if phrase.startswith("nonland"):
            kwargs["excluded_types"] = ("land",)
        return ObjectQuerySpec(**kwargs), exclude_source

    match = re.fullmatch(
        r"(?:(?P<quality>legendary|basic|tapped|untapped|token|nontoken|"
        r"white|blue|black|red|green|nonartifact|noncreature|nonenchantment|"
        r"nonland) )?(?P<body>[a-z]+(?:, [a-z]+)*(?:,? and [a-z]+)?)"
        r"(?P<flying> with flying)?",
        phrase,
    )
    if match is None:
        return None
    body = match.group("body")
    land_subtype = _BASIC_LAND_SUBTYPES.get(body.casefold())
    if land_subtype is not None:
        kwargs["types_all"] = ("land",)
        kwargs["subtypes_all"] = (land_subtype,)
    else:
        types = _split_type_list(body)
        if types is None:
            return None
        if len(types) == 1:
            kwargs["types_all"] = types
        else:
            kwargs["types_any"] = types
    quality = match.group("quality")
    if quality == "legendary":
        kwargs["supertypes_all"] = ("legendary",)
    elif quality == "basic":
        if kwargs.get("types_all") != ("land",):
            return None
        kwargs["supertypes_all"] = ("basic",)
    elif quality in {"tapped", "untapped"}:
        kwargs["tapped"] = quality == "tapped"
    elif quality in {"token", "nontoken"}:
        kwargs["token"] = quality == "token"
    elif quality in _COLOR_WORDS:
        kwargs["colors_any"] = (_COLOR_WORDS[quality],)
    elif quality and quality.startswith("non"):
        kwargs["excluded_types"] = (quality[3:],)
    if match.group("flying"):
        if kwargs.get("types_all") != ("creature",):
            return None
        kwargs["keywords_all"] = ("flying",)
    return ObjectQuerySpec(**kwargs), exclude_source


def mass_destruction_effect_template(
    text: str,
) -> MassDestructionEffectTemplate | None:
    split = _split_regeneration_prohibition(text)
    if split is None:
        return None
    destruction, prohibition_subject = split
    if prohibition_subject not in {
        None,
        "they",
        "those creatures",
        "creatures destroyed this way",
    }:
        return None
    match = re.fullmatch(
        r"destroy (?:all|each) (?P<subject>.+?)"
        r"(?P<controller> target opponent controls| target player controls|"
        r" you control| your opponents control)?\.?",
        destruction,
        re.IGNORECASE,
    )
    if match is None:
        return None
    parsed = fixed_affected_permanent_query(match.group("subject"))
    if parsed is None:
        return None
    query, exclude_source = parsed
    relation = PermanentControllerRelation.ANY
    target_controller: str | None = None
    target_schema: Mapping[str, Any] | None = None
    controller = (match.group("controller") or "").casefold()
    if controller == " you control":
        relation = PermanentControllerRelation.ACTOR
    elif controller == " your opponents control":
        relation = PermanentControllerRelation.OPPONENTS
    elif controller in {" target player controls", " target opponent controls"}:
        relation = PermanentControllerRelation.TARGET_PLAYER
        target_controller = "$target.0"
        target_schema = _player_target_schema(
            opponent=controller == " target opponent controls"
        )
    return MassDestructionEffectTemplate(
        spec=AffectedPermanentSetSpec(
            query=query,
            controller_relation=relation,
            target_controller=target_controller,
            exclude_source=exclude_source,
        ),
        target_schema=target_schema,
        regeneration_prohibited=prohibition_subject is not None,
    )


def destruction_effect_template(
    text: str,
) -> MassDestructionEffectTemplate | TargetedDestructionEffectTemplate | None:
    """Lower the closed targeted or fixed-set destruction grammar."""

    return (
        mass_destruction_effect_template(text)
        or targeted_destruction_effect_template(text)
    )


def targeted_destruction_effect_template(
    text: str,
) -> TargetedDestructionEffectTemplate | None:
    split = _split_regeneration_prohibition(text)
    if split is None:
        return None
    destruction, prohibition_subject = split
    if prohibition_subject not in {
        None,
        "it",
        "a creature destroyed this way",
    }:
        return None
    match = re.fullmatch(
        r"destroy (?P<subject>(?:another )?target .+?)\.?",
        destruction,
        re.IGNORECASE,
    )
    if match is None:
        return None
    target_spec = direct_permanent_target_spec(match.group("subject"))
    if target_spec is None:
        return None
    if prohibition_subject == "a creature destroyed this way" and not (
        target_spec.subtypes_any
        or target_spec.types_any == ("creature",)
        or "creature" in target_spec.types_all
    ):
        return None
    return TargetedDestructionEffectTemplate(
        target_spec,
        regeneration_prohibited=prohibition_subject is not None,
    )


__all__ = [
    "fixed_affected_permanent_query",
    "MassDestructionEffectTemplate",
    "TargetedDestructionEffectTemplate",
    "mass_destruction_effect_template",
    "targeted_destruction_effect_template",
]
