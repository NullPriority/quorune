"""Exact paid-X and public entrant binding for a separate reflexive trigger."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_CAPABILITY
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, ENTRANT_X_COUNTER_MARKER, REFLEXIVE_PAYMENT_CAPABILITY,
    ReflexiveTriggerProgram,
)
from ..replacement.immutable import FrozenMap


ENTRANT_X_PAYMENT_VERSION = 19
ENTRANT_X_CAPABILITY = "trigger.reflexive.entrant_announced_x_counters"
ENTRANT_X_MECHANIC = "entrant-announced-x-reflexive-counters"
ENTRANT_X_VARIANT = "another_controlled_creature_enters_for_announced_x"
ENTRANT_X_COUNTER_CONTEXT = "reflexive_paid_entrant"
ENTRANT_X_BODY = "you may pay {x}. when you do, put x +1/+1 counters on that creature."
ENTRANT_X_DEPENDENCIES = (
    ENTRANT_X_CAPABILITY, FIXED_EFFECT_PAYMENT_CAPABILITY, REFLEXIVE_PAYMENT_CAPABILITY,
    "counter.producer.fixed_effect", "trigger.event.normalized_zone_change", "trigger.placement.apnap",
)


def entrant_x_condition():
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "card", "op": "ne", "value": "$source.ref"},
    ]}


def entrant_x_child():
    return ReflexiveTriggerProgram(
        effects=({"op": "place_counters", ENTRANT_X_COUNTER_MARKER: True},),
        target_schema=None, mechanic_ids=("cr-122-counters",),
        schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION,
    )


def entrant_x_effect():
    return {"op": "offer_optional_mana_payment", "schema_version": ENTRANT_X_PAYMENT_VERSION,
            "player": "$controller", "reflexive_program": entrant_x_child().to_dict()}


def uses_entrant_x_payment(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "offer_optional_mana_payment"
            and effect.get("schema_version") == ENTRANT_X_PAYMENT_VERSION
            and "reflexive_program" in effect)


def validate_entrant_x_effect(effect, *, payer="$controller"):
    if (type(payer) is not str or not payer or not isinstance(effect, Mapping)
            or FrozenMap(effect) != FrozenMap({**entrant_x_effect(), "player": payer})
            or type(effect["schema_version"]) is not int):
        raise ValueError("Entrant payment requires its exact announced-X and reflexive shape")


def entrant_x_node_capabilities(*, effects, target_schema):
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        validate_entrant_x_effect(effects[0])
    except (TypeError, ValueError):
        return ()
    return ENTRANT_X_DEPENDENCIES


def entrant_x_payment_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(uses_entrant_x_payment(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = entrant_x_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not required or (program.event, program.active_zone) != ("creature.enter", "battlefield")
            or program.cost_schema is not None or program.handlers
            or FrozenMap(program.event_condition or {}) != FrozenMap(entrant_x_condition())):
        raise ValueError("Announced-X reflexive entry requires one originally controlled other creature occurrence")
    return required


@dataclass(frozen=True, slots=True)
class EnteredCreatureReceipt:
    ref: str
    logical_object_id: str
    zone_change_counter: int

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.ref, self.logical_object_id))
                or type(self.zone_change_counter) is not int or self.zone_change_counter < 1):
            raise ValueError("Reflexive counters require an original battlefield entrant")

    def to_dict(self):
        return {"ref": self.ref, "logical_object_id": self.logical_object_id,
                "zone_change_counter": self.zone_change_counter}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, Mapping) or set(data) != {"ref", "logical_object_id", "zone_change_counter"}:
            raise ValueError("The original creature entry receipt is malformed")
        return cls(**data)


def original_entrant_receipt(item):
    event = item.context
    if (item.kind not in {"triggered_ability", "triggered_ability_copy"}
            or event.get("to") != "battlefield" or event.get("from") == "battlefield"
            or "creature" not in event.get("types", ())):
        raise ValueError("Reflexive payment lost its original creature entry occurrence")
    return EnteredCreatureReceipt(event.get("card"), event.get("card_object_identity"),
                                  event.get("card_zone_change_counter"))


def bind_paid_entrant_counters(host, item, effect):
    if ENTRANT_X_COUNTER_MARKER not in effect:
        return effect
    if (FrozenMap(effect) != entrant_x_child().effects[0]
            or item.kind not in {"triggered_ability", "triggered_ability_copy"}
            or item.context.get("event") != "trigger.reflexive"):
        raise ValueError("Paid entrant counters require the original separate reflexive trigger")
    data = item.context.get(ENTRANT_X_COUNTER_CONTEXT)
    if not isinstance(data, Mapping) or set(data) != {"entrant", "paid_x"}:
        raise ValueError("Reflexive counters lost their original paid amount or entrant")
    receipt = EnteredCreatureReceipt.from_dict(data["entrant"])
    amount = data["paid_x"]
    if type(amount) is not int or amount < 0:
        raise ValueError("The paid mana amount must be an exact nonnegative integer")
    if amount == 0:
        return None
    current = next((card for card in host.state.cards.values() if card.ref == receipt.ref), None)
    if (current is None or current.zone != "battlefield" or current.phased_out
            or current.logical_object_id != receipt.logical_object_id
            or current.zone_change_counter != receipt.zone_change_counter):
        return None
    return {"op": "place_counters", "card": receipt.ref, "counter": "+1/+1", "amount": amount, "source": "$source"}
