"""Bind the paid variable Cycling occurrence to its independent token trigger."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..cycling_abilities import VARIABLE_CYCLING_CAPABILITY, VARIABLE_CYCLING_HANDLER_ID
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


RECEIPT_FIELD = "paid_cycling_x"
TOKEN_FIELD = "cycling_x_token_version"
TRIGGER_CAPABILITIES = (
    VARIABLE_CYCLING_CAPABILITY, "trigger.event.normalized_public_action",
    "trigger.placement.apnap", "token.creation.fixed_definition",
)


@dataclass(frozen=True, slots=True)
class PaidCyclingX:
    activation_stack_id: str
    source_incarnation: str
    source_ref: str
    x: int

    def __post_init__(self):
        if (
            any(type(value) is not str or not value or value.startswith("$")
                for value in (self.activation_stack_id, self.source_incarnation, self.source_ref))
            or type(self.x) is not int or self.x < 0
        ):
            raise ValueError("Cycling X requires its paid activation, source, and nonnegative integer")

    def to_dict(self):
        return {
            "schema_version": 1, "activation_stack_id": self.activation_stack_id,
            "source_incarnation": self.source_incarnation, "source_ref": self.source_ref, "x": self.x,
        }

    @classmethod
    def from_dict(cls, value):
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "activation_stack_id", "source_incarnation", "source_ref", "x"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
        ):
            raise ValueError("Cycling X occurrence has a closed version-one schema")
        return cls(value["activation_stack_id"], value["source_incarnation"], value["source_ref"], value["x"])


def paid_cycling_x_context(item, source):
    if item.source_object_id != source.object_id or item.context.get("source_logical_object_id") != source.logical_object_id:
        raise GameRuleError("Cycling X lost the hand incarnation paying its discard cost")
    receipt = PaidCyclingX(item.stack_id, source.logical_object_id, source.ref, item.x_value)
    return {RECEIPT_FIELD: receipt.to_dict()}


def variable_cycling_payment_program_dependencies(program):
    from ..activation_x import ANNOUNCED_ACTIVATION_X_CAPABILITY
    from ..semantic_runtime.cycling_abilities import VariableCyclingAbilityHandler

    handlers = {descriptor.get("handler_id"): descriptor for descriptor in program.handlers}
    scoped = handlers.get(VARIABLE_CYCLING_HANDLER_ID)
    if scoped is None:
        return ()
    catalog_id = "activation.catalog.pinned.v1"
    if (
        not set(handlers) <= {VARIABLE_CYCLING_HANDLER_ID, catalog_id} or len(handlers) != len(program.handlers)
        or (program.event, program.active_zone) != ("activate", "hand")
        or program.target_schema is not None or program.event_condition is not None
    ):
        raise ValueError("Variable Cycling requires its exact untargeted hand descriptor")
    spec = VariableCyclingAbilityHandler().validate(scoped)
    cost = program.cost_schema
    printed = spec.to_activated_ability()
    if (not isinstance(cost, Mapping) or cost.get("text") != printed.cost_text
            or not isinstance(cost.get("mana"), Mapping) or FrozenMap(cost["mana"]) != FrozenMap(printed.mana)
            or type(cost.get("announced_x_symbols")) is not int or cost["announced_x_symbols"] != spec.announced_x_symbols
            or cost.get("discard_source") is not True or tuple(program.effects) != (FrozenMap({
                "op": "draw", "player": "$controller", "count": 1, "private": True,
            }),)):
        raise ValueError("Variable Cycling requires the printed announced-X price and private draw")
    if catalog_id in handlers:
        entry = handlers[catalog_id].get("ability")
        if (not isinstance(entry, Mapping) or entry.get("ability_id") != printed.ability_id
                or entry.get("cost_text") != printed.cost_text or entry.get("discard_source") is not True
                or type(entry.get("announced_x_symbols")) is not int or entry["announced_x_symbols"] != spec.announced_x_symbols):
            raise ValueError("Variable Cycling requires its matching pinned activation")
    return (ANNOUNCED_ACTIVATION_X_CAPABILITY, VARIABLE_CYCLING_CAPABILITY)


def cycling_x_token_effect():
    return {"op": "create_token", TOKEN_FIELD: 1}


def cycling_x_token_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers)) if TOKEN_FIELD in effect)
    if not marked:
        return ()
    if (
        len(marked) != 1 or marked != tuple(program.effects) or program.handlers
        or type(marked[0][TOKEN_FIELD]) is not int or dict(marked[0]) != cycling_x_token_effect()
        or (program.event, program.active_zone) != ("card.cycled.self", "hand")
        or program.event_condition is not None or program.target_schema is not None or program.cost_schema is not None
        or not program.ability_id.startswith("trigger:")
    ):
        raise ValueError("Cycling X token requires the original untargeted self-Cycling trigger")
    return TRIGGER_CAPABILITIES


def is_closed_cycling_x_token_program(program):
    try:
        required = cycling_x_token_program_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def bind_cycling_x_token(host, item, effect):
    program = host.semantics.get(item.semantic_key)
    context = item.context
    if (
        item.kind not in {"triggered_ability", "triggered_ability_copy"}
        or context.get("currently_resolving") is not True or program is None
        or not host.semantic_program_is_current_trusted(program) or not is_closed_cycling_x_token_program(program)
        or tuple(program.effects) != (FrozenMap(effect),)
        or context.get("event") != "card.cycled" or context.get("cycling_kind") != "cycling"
        or context.get("source_zone") != "hand"
    ):
        raise GameRuleError("Cycling X token requires its paid original trigger")
    receipt = PaidCyclingX.from_dict(context.get(RECEIPT_FIELD))
    if (
        receipt.activation_stack_id != context.get("event_id") or receipt.source_ref != context.get("card")
        or receipt.source_incarnation.rpartition("@")[0] != item.source_object_id
    ):
        raise GameRuleError("Cycling X token lost the original paid occurrence")
    return {
        "op": "create_token", "controller": item.controller, "quantity": 1, "name": "Shark Token",
        "characteristics": {
            "type_line": "Token Creature — Shark", "colors": ["U"], "keywords": ["Flying"],
            "power": str(receipt.x), "toughness": str(receipt.x),
        },
    }
