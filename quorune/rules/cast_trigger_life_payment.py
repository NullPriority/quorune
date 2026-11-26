from __future__ import annotations

"""Typed prices for the any-player offer on a spell's own cast trigger."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..fractional_life_payment_model import FractionalLifePaymentSpec


@dataclass(frozen=True, slots=True)
class CastTriggerLifePaymentSpec:
    price: int | FractionalLifePaymentSpec
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported cast-trigger life payment version")
        if isinstance(self.price, FractionalLifePaymentSpec):
            return
        if type(self.price) is not int or self.price <= 0:
            raise ValueError("Fixed cast-trigger life payments require a positive integer")

    def amount(self, current_life: int) -> int:
        if type(current_life) is not int:
            raise ValueError("The prospective payer's life total must be an integer")
        if isinstance(self.price, FractionalLifePaymentSpec):
            return self.price.amount(max(0, current_life))
        return self.price

    def to_dict(self) -> dict:
        price = self.price if type(self.price) is int else self.price.to_dict()
        return {"schema_version": self.schema_version, "price": price}

    @classmethod
    def from_dict(cls, value) -> CastTriggerLifePaymentSpec:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "price"}:
            raise ValueError("Cast-trigger life payment fields are closed")
        price = value["price"]
        if isinstance(price, Mapping):
            price = FractionalLifePaymentSpec.from_dict(price)
        return cls(price, value["schema_version"])


CAST_TRIGGER_LIFE_PAYMENT_FIELD = "cast_trigger_life_payment"
CAST_TRIGGER_LIFE_PAYMENT_CAPABILITY = "stack.counter.own_cast_trigger_life_payment"


def cast_trigger_life_payment_condition():
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "card", "op": "eq", "value": "$source.ref"},
    ]}


def cast_trigger_life_payment_effect(spec: CastTriggerLifePaymentSpec) -> dict:
    return {"op": "choose_option", CAST_TRIGGER_LIFE_PAYMENT_FIELD: spec.to_dict()}


def cast_trigger_life_payment_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None:
        return ()
    effect = effects[0]
    if set(effect) != {"op", CAST_TRIGGER_LIFE_PAYMENT_FIELD} or effect["op"] != "choose_option":
        return ()
    try:
        spec = CastTriggerLifePaymentSpec.from_dict(effect[CAST_TRIGGER_LIFE_PAYMENT_FIELD])
    except (TypeError, ValueError):
        return ()
    from ..fractional_life_payment_model import FRACTIONAL_LIFE_PAYMENT_CAPABILITY

    extra = (FRACTIONAL_LIFE_PAYMENT_CAPABILITY,) if isinstance(spec.price, FractionalLifePaymentSpec) else ()
    return (CAST_TRIGGER_LIFE_PAYMENT_CAPABILITY, "stack.counter.effect", "trigger.placement.apnap", *extra)


def cast_trigger_life_payment_runtime_dependencies(program):
    from ..replacement.immutable import thaw_value

    if not any(CAST_TRIGGER_LIFE_PAYMENT_FIELD in effect for effect in program.effects):
        return ()
    required = cast_trigger_life_payment_node_capabilities(program.effects, program.target_schema)
    if (not required or (program.event, program.active_zone) != ("spell.cast", "stack")
            or program.cost_schema is not None or program.handlers or program.requires_arbiter
            or thaw_value(program.event_condition) != cast_trigger_life_payment_condition()):
        raise ValueError("The life offer requires its exact original self-cast trigger")
    return required


def materialize_cast_trigger_life_payment(effect, item):
    source = item.context.get("stack")
    if (item.context.get("event") != "spell.cast" or type(source) is not str or not source
            or source == item.ref or source.startswith("$")):
        raise ValueError("The life offer requires its normalized original cast spell reference")
    if "original_stack" in effect:
        if effect["original_stack"] != source:
            raise ValueError("The life offer cannot change its original cast spell")
        return dict(effect)
    if not cast_trigger_life_payment_node_capabilities((effect,), None):
        raise ValueError("The life offer requires its exact original price")
    return {**effect, "original_stack": source}
