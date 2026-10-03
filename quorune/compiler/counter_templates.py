from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from ..query_effect_amount_model import CastXAmountSpec
from ..replacement.immutable import FrozenMap
from ..rules.stack_controller_payment_cost import StackControllerPaymentCost, STACK_CONTROLLER_PAYMENT_MECHANIC
from ..util import mana_cost_to_vector

from .direct_target import (
    compiled_direct_target,
    direct_target_effect,
    direct_target_slug,
    stack_target_schema,
)


class CounterTarget(str, Enum):
    SPELL = "spell"
    NONCREATURE_SPELL = "noncreature spell"
    CREATURE_SPELL = "creature spell"
    CREATURE_OR_PLANESWALKER_SPELL = "creature or planeswalker spell"
    INSTANT_OR_SORCERY_SPELL = "instant or sorcery spell"
    SORCERY_SPELL = "sorcery spell"
    INSTANT_SPELL = "instant spell"
    ARTIFACT_OR_ENCHANTMENT_SPELL = "artifact or enchantment spell"
    ARTIFACT_SPELL = "artifact spell"
    CREATURE_OR_ENCHANTMENT_SPELL = "creature or enchantment spell"
    ARTIFACT_OR_CREATURE_SPELL = "artifact or creature spell"
    BLUE_SPELL = "blue spell"
    RED_SPELL = "red spell"
    GREEN_SPELL = "green spell"
    RED_OR_GREEN_SPELL = "red or green spell"
    NONBLUE_SPELL = "nonblue spell"
    COLORLESS_SPELL = "colorless spell"
    ACTIVATED_ABILITY = "activated ability"
    TRIGGERED_ABILITY = "triggered ability"
    ACTIVATED_OR_TRIGGERED_ABILITY = "activated or triggered ability"
    SPELL_OR_ABILITY = "spell, activated ability, or triggered ability"


_TYPE_DOMAINS: dict[CounterTarget, tuple[str, ...]] = {
    CounterTarget.CREATURE_SPELL: ("creature",),
    CounterTarget.CREATURE_OR_PLANESWALKER_SPELL: (
        "creature",
        "planeswalker",
    ),
    CounterTarget.INSTANT_OR_SORCERY_SPELL: ("instant", "sorcery"),
    CounterTarget.SORCERY_SPELL: ("sorcery",),
    CounterTarget.INSTANT_SPELL: ("instant",),
    CounterTarget.ARTIFACT_OR_ENCHANTMENT_SPELL: (
        "artifact",
        "enchantment",
    ),
    CounterTarget.ARTIFACT_SPELL: ("artifact",),
    CounterTarget.CREATURE_OR_ENCHANTMENT_SPELL: (
        "creature",
        "enchantment",
    ),
    CounterTarget.ARTIFACT_OR_CREATURE_SPELL: ("artifact", "creature"),
}
_COLOR_DOMAINS: dict[CounterTarget, tuple[str, ...]] = {
    CounterTarget.BLUE_SPELL: ("U",),
    CounterTarget.RED_SPELL: ("R",),
    CounterTarget.GREEN_SPELL: ("G",),
    CounterTarget.RED_OR_GREEN_SPELL: ("R", "G"),
}


@dataclass(frozen=True, slots=True)
class TargetedCounterEffectTemplate:
    """Closed lowering for one mandatory direct stack counter instruction."""

    target: CounterTarget

    def __post_init__(self) -> None:
        if not isinstance(self.target, CounterTarget):
            raise ValueError("Counter target domain is unsupported")

    @property
    def template_id(self) -> str:
        slug = direct_target_slug(self.target.value)
        return f"counter-target-{slug}-v2"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return direct_target_effect(
            "counter_stack_target",
            reference_field="stack",
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        categories = (
            ["spell", "ability"]
            if self.target is CounterTarget.SPELL_OR_ABILITY
            else ["ability"]
            if self.target in {
                CounterTarget.ACTIVATED_ABILITY,
                CounterTarget.TRIGGERED_ABILITY,
                CounterTarget.ACTIVATED_OR_TRIGGERED_ABILITY,
            }
            else ["spell"]
        )
        options: dict[str, Any] = {}
        if self.target in _TYPE_DOMAINS:
            options["types_any"] = _TYPE_DOMAINS[self.target]
        elif self.target is CounterTarget.NONCREATURE_SPELL:
            options["types_none"] = ("creature",)
        elif self.target in _COLOR_DOMAINS:
            options["colors_any"] = _COLOR_DOMAINS[self.target]
        elif self.target is CounterTarget.NONBLUE_SPELL:
            options["predicate"] = "nonblue_spell"
        elif self.target is CounterTarget.COLORLESS_SPELL:
            options["colorless"] = True
        elif self.target is CounterTarget.ACTIVATED_ABILITY:
            options["predicate"] = "activated_ability"
        elif self.target is CounterTarget.TRIGGERED_ABILITY:
            options["predicate"] = "triggered_ability"
        return stack_target_schema(categories=categories, **options)

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("counter", "cr-115-targets")

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


def targeted_counter_effect_template(
    text: str,
) -> TargetedCounterEffectTemplate | None:
    match = re.fullmatch(
        r"counter target (?P<target>spell|noncreature spell|creature spell|"
        r"creature or planeswalker spell|instant or sorcery spell|"
        r"sorcery spell|instant spell|artifact or enchantment spell|"
        r"artifact spell|creature or enchantment spell|"
        r"artifact or creature spell|blue spell|red spell|green spell|"
        r"red or green spell|nonblue spell|colorless spell|"
        r"activated ability|triggered ability|"
        r"activated or triggered ability|"
        r"spell, activated ability, or triggered ability)\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    return TargetedCounterEffectTemplate(
        CounterTarget(match.group("target").casefold())
    )


def is_intrinsically_uncounterable_spell(text: str) -> bool:
    """Recognize only the complete intrinsic counter prohibition sentence."""

    return bool(
        re.fullmatch(
            r"this spell can(?:not|'t) be countered\.?",
            text.strip(),
            re.IGNORECASE,
        )
    )


@dataclass(frozen=True, slots=True)
class TargetedControllerPaymentTemplate:
    counter: TargetedCounterEffectTemplate
    payment: StackControllerPaymentCost

    def __post_init__(self) -> None:
        if not isinstance(self.counter, TargetedCounterEffectTemplate) or not isinstance(self.payment, StackControllerPaymentCost):
            raise ValueError("Controller payment requires closed target and cost values")

    def compiled(self):
        return (
            f"counter-controller-payment-{direct_target_slug(self.counter.target.value)}-v2",
            ({"op": "counter_unless_pay", "schema_version": 2,
              "stack": "$target.0", "player": "$target.current_controller.0",
              "cost": self.payment.to_dict()},),
            self.counter.target_schema,
            (*self.counter.mechanics, STACK_CONTROLLER_PAYMENT_MECHANIC),
        )


def targeted_controller_payment_template(text: str, *, cast_x_available: bool = False) -> TargetedControllerPaymentTemplate | None:
    if type(cast_x_available) is not bool:
        raise ValueError("Announced-X availability must be a strict Boolean")
    match = re.fullmatch(
        r"(?P<body>Counter target .+?) unless its controller pays "
        r"(?P<cost>(?:\{(?:[0-9]+|[WUBRGCX])\})+)\.?", text.strip(), re.I,
    )
    if match is None:
        return None
    counter = targeted_counter_effect_template(match["body"] + ".")
    if counter is None:
        return None
    requirements, symbols = mana_cost_to_vector(match["cost"])
    if symbols:
        if not cast_x_available or tuple(symbols) != ("X",) or requirements["GENERIC"]:
            return None
        requirements["GENERIC"] = CastXAmountSpec().to_dict()
    return TargetedControllerPaymentTemplate(counter, StackControllerPaymentCost(FrozenMap(requirements)))


__all__ = [
    "CounterTarget",
    "TargetedCounterEffectTemplate",
    "TargetedControllerPaymentTemplate",
    "is_intrinsically_uncounterable_spell",
    "targeted_counter_effect_template",
    "targeted_controller_payment_template",
]
