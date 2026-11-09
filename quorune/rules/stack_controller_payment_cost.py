from __future__ import annotations

"""Closed costs for the existing target-controller payment choice."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..characteristic_fragments import (
    CharacteristicQuantityReduction, CharacteristicQuantityScope, CharacteristicQuantitySpec,
    characteristic_quantity_capabilities,
)
from ..public_characteristic_aggregate_model import CONTROLLED_CREATURES_POWER_QUERY
from ..query_effect_amount_model import (
    CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY, PUBLIC_QUERY_AMOUNT_CAPABILITY, PUBLIC_QUERY_AMOUNT_KIND,
    PublicQueryAmountSpec,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..scalar_effect_amount_model import (
    SCALAR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_KIND, ScalarAmountOrigin, ScalarEffectAmountSpec,
)


STACK_CONTROLLER_PAYMENT_MECHANIC = "counter-unless-controller-payment"
STACK_CONTROLLER_PAYMENT_CAPABILITY = "stack.counter.controller_payment"
STACK_CONTROLLER_QUANTITY_PAYMENT_CAPABILITY = "stack.counter.controller_quantity_payment"
STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION = 3
TARGET_SPELL_TAX_MANA_VALUE = FrozenMap({"kind": "counter_payment_target_mana_value", "schema_version": 1})
MANA_PAYMENT_KEYS = frozenset({"GENERIC", "W", "U", "B", "R", "G", "C"})
_PAYMENT_FIELDS = frozenset({"op", "schema_version", "stack", "player", "cost"})
_CONTINUATION_METADATA = frozenset({"_choice_actor", "_requirements", "_source_ref",
    "_source_logical_object_id", "_stack_label", "_countering_controller"})
_TARGET_GROUP_ROLE = re.compile(r"\$target_group\.(?:clause_[0-3]|target_[0-9]+)\.0")


def _quantity_operand_capabilities(value: Mapping[str, Any]) -> tuple[str, ...]:
    if value.get("kind") == PUBLIC_QUERY_AMOUNT_KIND:
        amount = PublicQueryAmountSpec.from_dict(value)
        quantity = amount.quantity
        if (amount.schema_version != 1 or amount.coefficient != 1
                or not isinstance(quantity, CharacteristicQuantitySpec) or quantity.schema_version not in {1, 2}
                or quantity.reduction not in {
                    CharacteristicQuantityReduction.OBJECT_COUNT,
                    CharacteristicQuantityReduction.DISTINCT_BASIC_LAND_TYPES,
                    CharacteristicQuantityReduction.DEVOTION,
                }
                or quantity.query is None
                or not (quantity.scope is CharacteristicQuantityScope.CONTROLLER_ZONE
                        or quantity.scope is CharacteristicQuantityScope.ALL_ZONES
                        and quantity.query.zones == ("battlefield",))):
            raise ValueError("Counter payment requires an independent public quantity")
        return (PUBLIC_QUERY_AMOUNT_CAPABILITY, *characteristic_quantity_capabilities(quantity))
    if value.get("kind") == SCALAR_AMOUNT_KIND:
        amount = ScalarEffectAmountSpec.from_dict(value)
        aggregate = amount.aggregate
        if (amount.origin is not ScalarAmountOrigin.PUBLIC_AGGREGATE or amount.coefficient != 1
                or amount.binding_id is not None or aggregate is None
                or (aggregate.characteristic, aggregate.reduction, aggregate.controller_relation)
                != ("power", "maximum", "source_controller") or aggregate.query != CONTROLLED_CREATURES_POWER_QUERY):
            raise ValueError("Counter payment requires the controlled creature power aggregate")
        return (SCALAR_AMOUNT_CAPABILITY, *aggregate.capability_dependencies)
    if value == TARGET_SPELL_TAX_MANA_VALUE and type(value.get("schema_version")) is int:
        return ()
    raise ValueError("Unsupported controller payment quantity")


@dataclass(frozen=True, slots=True)
class StackControllerPaymentCost:
    requirements: FrozenMap
    schema_version: int = 2

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {2, STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION}:
            raise ValueError("Controller payment cost version is unsupported")
        if not isinstance(self.requirements, Mapping) or set(self.requirements) != MANA_PAYMENT_KEYS:
            raise ValueError("Controller payment requires one complete ordinary mana vector")
        if self.schema_version == STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION:
            self._validate_quantity_cost()
        else:
            self._validate_fixed_cost()
        object.__setattr__(self, "requirements", FrozenMap(self.requirements))

    def _validate_fixed_cost(self) -> None:
        for color, amount in self.requirements.items():
            if isinstance(amount, Mapping):
                spec = CastXAmountSpec.from_dict(amount)
                if color != "GENERIC" or spec.coefficient != 1:
                    raise ValueError("Only positive announced X may supply generic payment")
            elif type(amount) is not int or amount < 0:
                raise ValueError("Controller payment amounts must be nonnegative integers")

    def _validate_quantity_cost(self) -> None:
        generic = self.requirements["GENERIC"]
        if not isinstance(generic, Mapping) or any(
            type(self.requirements[color]) is not int or self.requirements[color] != 0
            for color in MANA_PAYMENT_KEYS - {"GENERIC"}
        ):
            raise ValueError("Controller quantity payment requires one typed generic cost")
        _quantity_operand_capabilities(generic)

    @property
    def uses_cast_x(self) -> bool:
        return self.schema_version == 2 and isinstance(self.requirements["GENERIC"], Mapping)

    @property
    def capabilities(self) -> tuple[str, ...]:
        if self.schema_version == STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION:
            return (STACK_CONTROLLER_QUANTITY_PAYMENT_CAPABILITY,
                    *_quantity_operand_capabilities(self.requirements["GENERIC"]))
        return (DECLARED_AMOUNT_CAPABILITY,) if self.uses_cast_x else ()

    def to_dict(self) -> dict[str, Any]:
        return thaw_value(self.requirements)


def _is_payment_effect(effect: Mapping[str, Any]) -> bool:
    return (isinstance(effect, Mapping) and effect.get("op") == "counter_unless_pay"
            and type(effect.get("schema_version")) is int
            and effect["schema_version"] in {2, STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION})


def compiled_stack_controller_payment_cost(effect: Mapping[str, Any]) -> StackControllerPaymentCost:
    if not isinstance(effect, Mapping) or set(effect) != _PAYMENT_FIELDS:
        raise ValueError("Controller payment effect fields are malformed")
    if not _is_payment_effect(effect):
        raise ValueError("Controller payment effect identity is malformed")
    if effect["stack"] != "$target.0" or effect["player"] != "$target.current_controller.0":
        raise ValueError("Controller payment must refer to its current stack target")
    return StackControllerPaymentCost(effect["cost"], effect["schema_version"])


def resolved_stack_controller_payment_cost(effect: Mapping[str, Any]) -> dict[str, int]:
    if not isinstance(effect, Mapping) or not _PAYMENT_FIELDS <= set(effect) or set(effect) - _PAYMENT_FIELDS - _CONTINUATION_METADATA:
        raise ValueError("Resolved controller payment fields are malformed")
    public = {key: effect[key] for key in _PAYMENT_FIELDS}
    if public["op"] != "counter_unless_pay" or type(public["schema_version"]) is not int or public["schema_version"] != 2:
        raise ValueError("Resolved controller payment identity is malformed")
    if any(type(public[key]) is not str or not public[key] for key in ("stack", "player")):
        raise ValueError("Resolved controller payment requires a stack target and payer")
    if not isinstance(public["cost"], Mapping):
        raise ValueError("Resolved controller payment cost must be a vector")
    payment = StackControllerPaymentCost(FrozenMap(public["cost"]))
    if payment.uses_cast_x:
        raise ValueError("Announced-X payment must resolve through the canonical scalar owner")
    return payment.to_dict()


def _target_spell_mana_value(host, target) -> int:
    from ..selection.exile_cast import mana_value_of_cost

    card = host.state.cards.get(target.card_object_id or "")
    if card is None or card.zone != "stack":
        raise ValueError("Target spell lacks its current public face")
    data = host._effective_card_data(card)
    amount = mana_value_of_cost(str(data.get("mana_cost") or ""), x_value=target.x_value or 0)
    if not amount.is_integer():
        raise ValueError("The target spell requires an integer mana value")
    return int(amount)


def bind_stack_controller_quantity_payment(host, effect, *, resolve):
    """Bind the live amount once before the payer can activate mana abilities."""
    if not _is_payment_effect(effect) or set(effect) != _PAYMENT_FIELDS:
        raise ValueError("Controller quantity payment fields are malformed")
    stack = effect["stack"]
    if stack == "$target.0":
        payer_reference = "$target.current_controller.0"
    elif type(stack) is str and _TARGET_GROUP_ROLE.fullmatch(stack):
        payer_reference = stack[:-2] + ".current_controller.0"
    else:
        raise ValueError("Controller quantity payment requires a declared spell target")
    if effect["player"] != payer_reference or effect["schema_version"] != STACK_CONTROLLER_QUANTITY_PAYMENT_VERSION:
        raise ValueError("Controller quantity payment lost its target-controller binding")
    cost = StackControllerPaymentCost(effect["cost"], effect["schema_version"])
    target_ref = resolve(stack)
    if target_ref is None:
        return {**effect, "stack": None}
    payer = resolve(payer_reference)
    target = next((row for row in host.state.stack if row.ref == target_ref), None)
    if target is None or target.kind not in {"spell", "spell_copy"} or target.controller != payer:
        raise ValueError("Controller quantity payment requires its current legal spell and payer")
    amount = cost.requirements["GENERIC"]
    generic = _target_spell_mana_value(host, target) if amount == TARGET_SPELL_TAX_MANA_VALUE else resolve(amount)
    if type(generic) is not int or generic < 0:
        raise ValueError("Controller payment requires a known nonnegative integer")
    return {**effect, "schema_version": 2, "stack": target_ref, "player": payer,
            "cost": {**cost.to_dict(), "GENERIC": generic}}
