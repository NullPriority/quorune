from __future__ import annotations

"""Closed costs for the existing target-controller payment choice."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
from ..replacement.immutable import FrozenMap, thaw_value


STACK_CONTROLLER_PAYMENT_MECHANIC = "counter-unless-controller-payment"
STACK_CONTROLLER_PAYMENT_CAPABILITY = "stack.counter.controller_payment"
MANA_PAYMENT_KEYS = frozenset({"GENERIC", "W", "U", "B", "R", "G", "C"})
_PAYMENT_FIELDS = frozenset({"op", "schema_version", "stack", "player", "cost"})
_CONTINUATION_METADATA = frozenset({"_choice_actor", "_requirements", "_source_ref",
    "_source_logical_object_id", "_stack_label", "_countering_controller"})


@dataclass(frozen=True, slots=True)
class StackControllerPaymentCost:
    requirements: FrozenMap

    def __post_init__(self) -> None:
        if not isinstance(self.requirements, Mapping) or set(self.requirements) != MANA_PAYMENT_KEYS:
            raise ValueError("Controller payment requires one complete ordinary mana vector")
        for color, amount in self.requirements.items():
            if isinstance(amount, Mapping):
                spec = CastXAmountSpec.from_dict(amount)
                if color != "GENERIC" or spec.coefficient != 1:
                    raise ValueError("Only positive announced X may supply generic payment")
            elif type(amount) is not int or amount < 0:
                raise ValueError("Controller payment amounts must be nonnegative integers")
        object.__setattr__(self, "requirements", FrozenMap(self.requirements))

    @property
    def uses_cast_x(self) -> bool:
        return isinstance(self.requirements["GENERIC"], Mapping)

    @property
    def capabilities(self) -> tuple[str, ...]:
        return (DECLARED_AMOUNT_CAPABILITY,) if self.uses_cast_x else ()

    def to_dict(self) -> dict[str, Any]:
        return thaw_value(self.requirements)


def compiled_stack_controller_payment_cost(effect: Mapping[str, Any]) -> StackControllerPaymentCost:
    if not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "stack", "player", "cost"}:
        raise ValueError("Controller payment effect fields are malformed")
    if effect["op"] != "counter_unless_pay" or type(effect["schema_version"]) is not int or effect["schema_version"] != 2:
        raise ValueError("Controller payment effect identity is malformed")
    if effect["stack"] != "$target.0" or effect["player"] != "$target.current_controller.0":
        raise ValueError("Controller payment must refer to its current stack target")
    if not isinstance(effect["cost"], Mapping):
        raise ValueError("Controller payment cost must be a mana vector")
    return StackControllerPaymentCost(FrozenMap(effect["cost"]))


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
