from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping

from ..query_effect_amount_model import CastXAmountSpec
from ..replacement.immutable import FrozenMap
from ..rules.stack_controller_payment_cost import (
    MANA_PAYMENT_KEYS, STACK_CONTROLLER_PAYMENT_MECHANIC, STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION,
    StackControllerPaymentCost,
)
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
    TRIGGERED_ABILITY_OR_COLORLESS_SPELL = "triggered ability or colorless spell"
    ABILITY_OR_NONCREATURE_SPELL = "activated ability, triggered ability, or noncreature spell"
    ABILITY_OR_LEGENDARY_SPELL = "activated ability, triggered ability, or legendary spell"
    BLUE_INSTANT_SPELL = "blue instant spell"
    ACTIVATED_ARTIFACT_SOURCE = "activated ability from an artifact source"
    NONCREATURE_ABILITY_SOURCE = "activated or triggered ability from a noncreature source"


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
_MIXED_STACK_PREDICATES = {
    CounterTarget.TRIGGERED_ABILITY_OR_COLORLESS_SPELL: 'triggered_ability_or_colorless_spell',
    CounterTarget.ABILITY_OR_NONCREATURE_SPELL: 'ability_or_noncreature_spell',
    CounterTarget.ABILITY_OR_LEGENDARY_SPELL: 'ability_or_legendary_spell',
    CounterTarget.BLUE_INSTANT_SPELL: 'blue_instant_spell',
    CounterTarget.ACTIVATED_ARTIFACT_SOURCE: 'activated_artifact_source',
    CounterTarget.NONCREATURE_ABILITY_SOURCE: 'noncreature_ability_source',
}


@dataclass(frozen=True, slots=True)
class TargetedCounterEffectTemplate:
    """Closed lowering for one mandatory direct stack counter instruction."""

    target: CounterTarget
    mana_value_predicate: tuple[str, int] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, CounterTarget):
            raise ValueError("Counter target domain is unsupported")
        if self.mana_value_predicate is not None:
            if type(self.mana_value_predicate) is not tuple or len(self.mana_value_predicate) != 2:
                raise ValueError("Fixed stack mana-value predicate must be an immutable field/value pair")
            field, value = self.mana_value_predicate
            if (field not in {"mana_value", "mana_value_min", "mana_value_max"}
                    or type(value) is not int or value < 0
                    or "ability" in self.target.value):
                raise ValueError("Fixed stack mana value requires one spell target and nonnegative integer bound")

    @property
    def template_id(self) -> str:
        slug = direct_target_slug(self.target.value)
        if self.mana_value_predicate is not None:
            field, value = self.mana_value_predicate
            slug += f"-{field.replace('_', '-')}-{value}"
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
            if self.target is CounterTarget.SPELL_OR_ABILITY or self.target in {
                CounterTarget.TRIGGERED_ABILITY_OR_COLORLESS_SPELL,
                CounterTarget.ABILITY_OR_NONCREATURE_SPELL,
                CounterTarget.ABILITY_OR_LEGENDARY_SPELL,
            }
            else ["ability"]
            if self.target in {
                CounterTarget.ACTIVATED_ABILITY,
                CounterTarget.TRIGGERED_ABILITY,
                CounterTarget.ACTIVATED_OR_TRIGGERED_ABILITY,
                CounterTarget.ACTIVATED_ARTIFACT_SOURCE,
                CounterTarget.NONCREATURE_ABILITY_SOURCE,
            }
            else ["spell"]
        )
        options: dict[str, Any] = {}
        if self.target in _MIXED_STACK_PREDICATES:
            options['predicate'] = _MIXED_STACK_PREDICATES[self.target]
        elif self.target in _TYPE_DOMAINS:
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
        schema = dict(stack_target_schema(categories=categories, **options))
        if self.mana_value_predicate is not None:
            field, value = self.mana_value_predicate
            schema[field] = value
        return schema

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
    normalized = text.strip()
    quantity = re.search(r" with mana value (?P<value>[0-9]+)(?P<comparison> or less| or greater)?\.?$", normalized, re.I)
    numeric = None
    if quantity is not None:
        comparison = (quantity["comparison"] or "").casefold()
        field = "mana_value_max" if comparison == " or less" else "mana_value_min" if comparison == " or greater" else "mana_value"
        numeric = (field, int(quantity["value"]))
        normalized = normalized[:quantity.start()] + "."
    match = re.fullmatch(
        r"counter target (?P<target>spell|noncreature spell|creature spell|"
        r"creature or planeswalker spell|instant or sorcery spell|"
        r"sorcery spell|instant spell|artifact or enchantment spell|"
        r"artifact spell|creature or enchantment spell|"
        r"artifact or creature spell|blue spell|red spell|green spell|"
        r"red or green spell|nonblue spell|colorless spell|"
        r"activated ability|triggered ability|"
        r"activated or triggered ability|"
        r"spell, activated ability, or triggered ability|"
        r"triggered ability or colorless spell|"
        r"activated ability, triggered ability, or noncreature spell|"
        r"activated ability, triggered ability, or legendary spell|"
        r"blue instant spell|activated ability from an artifact source|"
        r"activated or triggered ability from a noncreature source)\.?",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    if numeric is not None and "ability" in match["target"].casefold():
        return None
    return TargetedCounterEffectTemplate(
        CounterTarget(match.group("target").casefold()), mana_value_predicate=numeric,
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
            f"counter-controller-payment-{self.counter.template_id.removeprefix('counter-target-').removesuffix('-v2')}-v{self.payment.schema_version}",
            ({"op": "counter_unless_pay", "schema_version": self.payment.schema_version,
              "stack": "$target.0", "player": "$target.current_controller.0",
              "cost": self.payment.to_dict()},),
            self.counter.target_schema,
            (*self.counter.mechanics, STACK_CONTROLLER_PAYMENT_MECHANIC),
        )



_QUANTITY_PAYMENT_COUNT = re.compile(
    r"(?:\{(?P<base>[1-9][0-9]*)\} plus an additional )?"
    r"\{(?P<multiplier>[1-9][0-9]*)\} for each (?P<subject>.+)",
    re.I,
)
_QUANTITY_PAYMENT_X = re.compile(r"\{X\}, where X is (?P<value>.+)", re.I)


def _controller_payment_operand(text, *, source_name):
    from ..query_effect_amount_model import PublicQueryAmountSpec
    from ..rules.stack_controller_payment_cost import TARGET_SPELL_TAX_MANA_VALUE
    from .collection_quantity_templates import public_card_quantity
    from .query_characteristic_templates import query_characteristic_quantity, query_characteristic_value

    count = _QUANTITY_PAYMENT_COUNT.fullmatch(text)
    if count is not None:
        subject = count["subject"]
        quantity = public_card_quantity(query_characteristic_quantity(subject, source_name=source_name), subject)
        if quantity is None:
            return None
        return PublicQueryAmountSpec(replace(
            quantity, schema_version=2, multiplier=int(count["multiplier"]), offset=int(count["base"] or 0),
        )).to_dict()
    definition = _QUANTITY_PAYMENT_X.fullmatch(text)
    if definition is None:
        return None
    value = definition["value"]
    if value.casefold() == "its mana value":
        return TARGET_SPELL_TAX_MANA_VALUE
    if value.casefold() == "the greatest power among creatures you control":
        from ..public_characteristic_aggregate_model import CONTROLLED_CREATURES_POWER_QUERY, PublicCharacteristicAggregateSpec
        from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

        aggregate = PublicCharacteristicAggregateSpec("power", "maximum", "source_controller", CONTROLLED_CREATURES_POWER_QUERY)
        return ScalarEffectAmountSpec(ScalarAmountOrigin.PUBLIC_AGGREGATE, schema_version=3, aggregate=aggregate).to_dict()
    quantity = query_characteristic_value(value, source_name=source_name)
    return PublicQueryAmountSpec(quantity).to_dict() if quantity is not None else None


def _quantity_controller_payment_template(text, *, source_name):
    match = re.fullmatch(
        r"(?P<body>Counter target .+?) unless its controller pays (?P<amount>.+?)\.?", text.strip(), re.I,
    )
    if match is None:
        return None
    counter = targeted_counter_effect_template(match["body"] + ".")
    if counter is None or counter.target_schema.get("categories") != ["spell"]:
        return None
    try:
        amount = _controller_payment_operand(match["amount"], source_name=source_name)
        if amount is None:
            return None
        cost = {key: 0 for key in MANA_PAYMENT_KEYS}
        cost["GENERIC"] = amount
        return TargetedControllerPaymentTemplate(
            counter, StackControllerPaymentCost(cost, STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION),
        )
    except (TypeError, ValueError):
        return None


def targeted_controller_payment_template(
    text: str, *, cast_x_available: bool = False, source_name: str = "",
) -> TargetedControllerPaymentTemplate | None:
    if type(cast_x_available) is not bool:
        raise ValueError("Announced-X availability must be a strict Boolean")
    match = re.fullmatch(
        r"(?P<body>Counter target .+?) unless its controller pays "
        r"(?P<cost>(?:\{(?:[0-9]+|[WUBRGCX])\})+)\.?", text.strip(), re.I,
    )
    if match is None:
        return _quantity_controller_payment_template(text, source_name=source_name)
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
