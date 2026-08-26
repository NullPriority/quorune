from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ..rules.graveyard_card_targets import (
    GraveyardCardTargetKind,
    OwnGraveyardCardTargetSpec,
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
            or self.target_spec.combat_state is None
            or self.target is not ReturnToHandTarget.CREATURE
        ):
            raise ValueError(
                "Return-to-hand direct target requires one combat-state creature"
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

    target: GraveyardCardTargetKind

    def __post_init__(self) -> None:
        if not isinstance(self.target, GraveyardCardTargetKind):
            raise ValueError("Graveyard return target domain is unsupported")

    @property
    def template_id(self) -> str:
        slug = OwnGraveyardCardTargetSpec(self.target).slug
        return f"return-target-{slug}-from-own-graveyard-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return direct_target_effect(
            "return_graveyard_card_to_owner_hand",
            reference_field="card",
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return OwnGraveyardCardTargetSpec(self.target).to_target_schema()

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
            r"return (?P<subject>target .+) to its owner['’]s hand\.?",
            text.strip(),
            re.IGNORECASE,
        )
        if direct is None:
            return None
        target_spec = direct_permanent_target_spec(direct.group("subject"))
        if target_spec is None or target_spec.combat_state is None:
            return None
        return TargetedReturnToHandEffectTemplate(
            ReturnToHandTarget.CREATURE,
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
    if match is None:
        return None
    return TargetedOwnGraveyardReturnToHandEffectTemplate(
        GraveyardCardTargetKind(match.group("target").casefold())
    )


__all__ = [
    "GraveyardCardTargetKind",
    "ReturnToHandTarget",
    "TargetedOwnGraveyardReturnToHandEffectTemplate",
    "TargetedReturnToHandEffectTemplate",
    "targeted_own_graveyard_return_to_hand_effect_template",
    "targeted_return_to_hand_effect_template",
]
