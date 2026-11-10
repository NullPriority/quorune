from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ..rules.graveyard_card_targets import (
    GraveyardCardTargetKind,
    OPPOSING_GRAVEYARD_RETURN_MECHANIC,
    OwnGraveyardCardTargetSpec,
    PUBLIC_GRAVEYARD_RETURN_KINDS,
    PublicGraveyardCardTargetKind,
    PublicGraveyardCardTargetSpec,
    opposing_graveyard_return_sequence,
    opposing_graveyard_return_sequence_capabilities,
    public_graveyard_return_effect,
)
from .direct_target import (
    DirectPermanentTargetSpec,
    compiled_direct_target,
    direct_permanent_target_spec,
    direct_target_effect,
    permanent_target_schema,
)


class ReturnToHandTarget(str, Enum):
    ARTIFACT = "artifact"
    CREATURE = "creature"
    ENCHANTMENT = "enchantment"
    LAND = "land"
    NONLAND_PERMANENT = "nonland permanent"
    PERMANENT = "permanent"
    ARTIFACT_OR_ENCHANTMENT = "artifact or enchantment"
    CREATURE_OR_PLANESWALKER = "creature or planeswalker"

    @property
    def card_types(self) -> tuple[str, ...]:
        if self is ReturnToHandTarget.NONLAND_PERMANENT:
            return ()
        return tuple(self.value.split(" or "))


@dataclass(frozen=True, slots=True)
class TargetedReturnToHandEffectTemplate:
    """Closed lowering for one mandatory direct battlefield return."""

    target: ReturnToHandTarget
    target_spec: DirectPermanentTargetSpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, ReturnToHandTarget):
            raise ValueError("Return-to-hand target domain is unsupported")
        if self.target_spec is not None and (
            not isinstance(self.target_spec, DirectPermanentTargetSpec)
            or self.target is not ReturnToHandTarget.PERMANENT
        ):
            raise ValueError(
                "Return-to-hand direct target requires one closed permanent predicate"
            )

    @property
    def template_id(self) -> str:
        if self.target_spec is not None:
            return f"return-target-{self.target_spec.slug}-v2"
        return "return-target-" + self.target.value.replace(" ", "-") + "-v2"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return direct_target_effect("bounce", reference_field="card")

    @property
    def target_schema(self) -> Mapping[str, Any]:
        if self.target_spec is not None:
            return self.target_spec.to_target_schema()
        return permanent_target_schema(
            types_none=(
                ("land",)
                if self.target is ReturnToHandTarget.NONLAND_PERMANENT
                else ()
            ),
            types_any=(
                self.target.card_types
                if self.target
                not in {
                    ReturnToHandTarget.NONLAND_PERMANENT,
                    ReturnToHandTarget.PERMANENT,
                }
                else ()
            ),
        )

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("return-to-owner-hand", "cr-115-targets")

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
class TargetedOwnGraveyardReturnToHandEffectTemplate:
    """Closed lowering for one mandatory own-graveyard card return."""

    target: GraveyardCardTargetKind | OwnGraveyardCardTargetSpec

    def __post_init__(self) -> None:
        if not isinstance(
            self.target,
            (GraveyardCardTargetKind, OwnGraveyardCardTargetSpec),
        ):
            raise ValueError("Graveyard return target domain is unsupported")

    @property
    def target_spec(self) -> OwnGraveyardCardTargetSpec:
        return (
            self.target
            if isinstance(self.target, OwnGraveyardCardTargetSpec)
            else OwnGraveyardCardTargetSpec(self.target)
        )

    @property
    def template_id(self) -> str:
        version = 1 if isinstance(self.target, GraveyardCardTargetKind) else 2
        return (
            f"return-target-{self.target_spec.slug}-from-own-graveyard-v{version}"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return direct_target_effect(
            "return_graveyard_card_to_owner_hand",
            reference_field="card",
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return self.target_spec.to_target_schema()

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("return-to-owner-hand", "cr-115-targets")

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
class TargetedPublicGraveyardReturnToHandEffectTemplate:
    target: PublicGraveyardCardTargetSpec

    def compiled(self):
        return compiled_direct_target(
            template_id=f"return-target-{self.target.slug}-from-public-graveyard-v1",
            effects=(public_graveyard_return_effect(),),
            target_schema=self.target.to_target_schema(),
            mechanics=("return-to-owner-hand", "cr-115-targets"),
        )


_PUBLIC_GRAVEYARD_RETURN = re.compile(
    r"return target (?P<target>" + "|".join(re.escape(kind.value) for kind in PUBLIC_GRAVEYARD_RETURN_KINDS)
    + r") from a graveyard to its owner['’]s hand\.?",
    re.IGNORECASE,
)


_OPPOSING_GRAVEYARD_RETURN = re.compile(
    r"Return (?:(?P<count>two|three|four|five|six) target cards|target card) "
    r"from an opponent['’]s graveyard to their hand\. (?P<follower>.+)",
    re.IGNORECASE,
)


def opposing_graveyard_return_sequence_template(text, *, compile_result):
    match = _OPPOSING_GRAVEYARD_RETURN.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    from .fixed_numbers import fixed_number

    template, effects, schema, mechanics = compile_result(match["follower"])
    if template is None or len(effects) != 1 or schema is None:
        return None
    try:
        count = fixed_number(match["count"]) if match["count"] is not None else 1
        effects, schema = opposing_graveyard_return_sequence(count, effects[0], schema)
    except (TypeError, ValueError):
        return None
    if not opposing_graveyard_return_sequence_capabilities(effects=effects, target_schema=schema):
        return None
    return "opposing-graveyard-return-then-targeted-result-v1", effects, schema, tuple(
        dict.fromkeys((OPPOSING_GRAVEYARD_RETURN_MECHANIC, "cr-115-targets", *mechanics)),
    )


def targeted_public_graveyard_return_to_hand_effect_template(
    text: str,
) -> TargetedPublicGraveyardReturnToHandEffectTemplate | None:
    match = _PUBLIC_GRAVEYARD_RETURN.fullmatch(text.strip())
    if match is None:
        return None
    kind = PublicGraveyardCardTargetKind(match["target"].casefold())
    return TargetedPublicGraveyardReturnToHandEffectTemplate(PublicGraveyardCardTargetSpec(kind))


def targeted_return_to_hand_effect_template(
    text: str,
) -> TargetedReturnToHandEffectTemplate | None:
    match = re.fullmatch(
        r"return target (?P<target>artifact|creature|enchantment|land|"
        r"nonland permanent|permanent|artifact or enchantment|"
        r"creature or planeswalker) to its owner['’]s hand\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        direct = re.fullmatch(
            r"return (?P<subject>(?:another )?target .+) to "
            r"(?:its|their) owner['’]s hand\.?",
            text.strip(),
            re.IGNORECASE,
        )
        if direct is None:
            return None
        target_spec = direct_permanent_target_spec(direct.group("subject"))
        if target_spec is None:
            return None
        return TargetedReturnToHandEffectTemplate(
            ReturnToHandTarget.PERMANENT,
            target_spec=target_spec,
        )
    return TargetedReturnToHandEffectTemplate(
        ReturnToHandTarget(match.group("target").casefold())
    )


def targeted_own_graveyard_return_to_hand_effect_template(
    text: str,
) -> TargetedOwnGraveyardReturnToHandEffectTemplate | None:
    targets = "|".join(
        re.escape(kind.value) for kind in GraveyardCardTargetKind
    )
    match = re.fullmatch(
        rf"return target (?P<target>{targets}) "
        r"from your graveyard to your hand\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is not None:
        return TargetedOwnGraveyardReturnToHandEffectTemplate(
            GraveyardCardTargetKind(match.group("target").casefold())
        )
    expanded = re.fullmatch(
        r"return target (?P<target>.+?) card"
        r"(?: with mana value (?P<maximum>[0-9]+) or less)? from your graveyard to your "
        r"hand\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if expanded is None:
        return None
    description = expanded.group("target")
    simple = next((kind for kind in GraveyardCardTargetKind
        if kind.value.casefold() == description.casefold() + ' card'), None)
    target_spec = (OwnGraveyardCardTargetSpec(simple) if simple is not None
        else _own_graveyard_characteristic_target(description))
    if target_spec is not None and expanded.group("maximum") is not None:
        if simple is GraveyardCardTargetKind.NONLAND_PERMANENT_CARD:
            target_spec = OwnGraveyardCardTargetSpec(
                GraveyardCardTargetKind.PERMANENT_CARD, types_none=('land',))
        schema = target_spec.to_target_schema()
        schema['mana_value_max'] = int(expanded.group("maximum"))
        try:
            target_spec = OwnGraveyardCardTargetSpec.from_target_schema(schema)
        except (TypeError, ValueError):
            return None
    return (
        TargetedOwnGraveyardReturnToHandEffectTemplate(target_spec)
        if target_spec is not None
        else None
    )


def _own_graveyard_characteristic_target(
    description: str,
) -> OwnGraveyardCardTargetSpec | None:
    """Reuse the closed direct characteristic grammar for an owned card."""

    normalized = " ".join(description.casefold().split())
    special: dict[str, dict[str, Any]] = {
        "green": {"colors_any": ("G",)},
        "multicolored": {"color_count_min": 2},
        "noncreature, nonland": {
            "types_none": ("creature", "land"),
        },
        "aura or equipment": {
            "subtypes_any": ("aura", "equipment"),
        },
        "equipment or vehicle": {
            "subtypes_any": ("equipment", "vehicle"),
        },
    }
    fields = special.get(normalized)
    if fields is not None:
        try:
            return OwnGraveyardCardTargetSpec(None, **fields)
        except (TypeError, ValueError):
            return None

    direct = direct_permanent_target_spec(f"target {description}")
    if direct is None or any(
        (
            direct.controller_relation != "any",
            direct.source_exclusion,
            direct.state_predicate is not None,
            direct.subtypes_none,
            direct.keywords_all,
            direct.keywords_none,
            direct.colorless is not None,
            direct.token is not None,
            direct.commander is not None,
            direct.combat_state is not None,
            direct.damage_history is not None,
            direct.numeric_characteristic is not None,
            direct.mana_value_min is not None,
            direct.mana_value_max is not None,
            direct.mana_value_equal is not None,
        )
    ):
        return None
    try:
        permanent_domain = (
            normalized.endswith(" permanent")
            and not direct.types_any
            and not direct.types_all
        )
        return OwnGraveyardCardTargetSpec(
            (
                GraveyardCardTargetKind.PERMANENT_CARD
                if permanent_domain
                else None
            ),
            types_any=direct.types_any,
            types_all=direct.types_all,
            types_none=direct.types_none,
            subtypes_any=direct.subtypes_any,
            supertypes_any=direct.supertypes_any,
            supertypes_none=direct.supertypes_none,
            colors_any=direct.colors_any,
            colors_none=direct.colors_none,
            color_count_min=direct.color_count_min,
            color_count_equal=direct.color_count_equal,
        )
    except (TypeError, ValueError):
        return None


__all__ = [
    "GraveyardCardTargetKind",
    "ReturnToHandTarget",
    "TargetedOwnGraveyardReturnToHandEffectTemplate",
    "TargetedPublicGraveyardReturnToHandEffectTemplate",
    "TargetedReturnToHandEffectTemplate",
    "targeted_own_graveyard_return_to_hand_effect_template",
    "targeted_public_graveyard_return_to_hand_effect_template",
    "targeted_return_to_hand_effect_template",
]
