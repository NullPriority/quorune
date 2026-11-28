"""An ordered life result bound to the recipient's committed combat damage."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..received_damage_model import RECEIVED_COMBAT_DAMAGE_EVENT, RECEIVED_DAMAGE_CAPABILITY
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import contains_nested_key


COMBAT_RECEIVING_LIFE_TEMPLATE = "combat-receiving-life-and-attacker-loss-v1"
COMBAT_RECEIVING_LIFE_FIELD = "combat_receiving_life_phase"
COMBAT_RECEIVING_LIFE_CONTEXT = "combat_receiving_life_receipt"
_REQUIRED = (
    RECEIVED_DAMAGE_CAPABILITY,
    "trigger.effect.fixed_event",
    "trigger.placement.apnap",
    "life.change.effect",
)


def combat_receiving_life_effects():
    return (
        {"op": "life", "schema_version": 1, COMBAT_RECEIVING_LIFE_FIELD: "controller_gain"},
        {"op": "lose_life", "schema_version": 1, COMBAT_RECEIVING_LIFE_FIELD: "attacker_loss"},
    )


def uses_combat_receiving_life(value):
    return contains_nested_key(value, COMBAT_RECEIVING_LIFE_FIELD)


def combat_receiving_life_node_capabilities(effects, target_schema):
    if (target_schema is None and tuple(effects) == combat_receiving_life_effects()
            and all(type(effect["schema_version"]) is int for effect in effects)):
        return _REQUIRED
    return ()


def combat_receiving_life_runtime_dependencies(program):
    if not uses_combat_receiving_life((program.effects, program.handlers)):
        return ()
    condition = {"all": [
        {"field": "target", "op": "eq", "value": "$source.ref"},
        {"field": "combat", "op": "eq", "value": True},
    ]}
    required = combat_receiving_life_node_capabilities(program.effects, program.target_schema)
    if (
        not required or (program.event, program.active_zone) != (RECEIVED_COMBAT_DAMAGE_EVENT, "battlefield")
        or thaw_value(program.event_condition) != condition
        or program.cost_schema is not None or program.handlers
    ):
        raise ValueError("Combat receiving life requires its own exact grouped battlefield damage trigger")
    return required


@dataclass(frozen=True, slots=True)
class CombatReceivingLifeReceipt:
    event_id: str
    recipient_incarnation: str
    attacking_player: str
    amount: int

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (
                self.event_id, self.recipient_incarnation, self.attacking_player))
                or type(self.amount) is not int or self.amount < 1):
            raise ValueError("Combat receiving life requires its committed occurrence, attacker and amount")

    def to_dict(self):
        return {"schema_version": 1, "event_id": self.event_id,
                "recipient_incarnation": self.recipient_incarnation,
                "attacking_player": self.attacking_player, "amount": self.amount}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {
                "schema_version", "event_id", "recipient_incarnation", "attacking_player", "amount"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Combat receiving life requires an exact typed receipt")
        return cls(value["event_id"], value["recipient_incarnation"], value["attacking_player"], value["amount"])

    def matches(self, event, context):
        return (
            event == RECEIVED_COMBAT_DAMAGE_EVENT and context.get("combat") is True
            and context.get("target_kind") == "permanent"
            and context.get("event_id") == self.event_id
            and context.get("target_logical_object_id") == self.recipient_incarnation
            and type(context.get("amount")) is int and type(context.get("combat_amount")) is int
            and context["amount"] == context["combat_amount"] == self.amount
        )


def combat_receiving_life_trigger_context(host, program, event, context):
    if event != RECEIVED_COMBAT_DAMAGE_EVENT or not combat_receiving_life_runtime_dependencies(program):
        return {}
    # A single active player is the attacking player when combat damage is dealt.
    attacker = host.state.active_player
    receipt = CombatReceivingLifeReceipt(
        context.get("event_id"), context.get("target_logical_object_id"), attacker, context.get("amount"),
    )
    if attacker not in host.state.players or not receipt.matches(event, context):
        raise ValueError("The received combat trigger lost its original attacking player or damage")
    return {COMBAT_RECEIVING_LIFE_CONTEXT: receipt.to_dict()}


def bind_combat_receiving_life(host, item, effect):
    if COMBAT_RECEIVING_LIFE_FIELD not in effect:
        return effect
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (
        program is None or program.trust_level != "trusted" or item.kind != "triggered_ability"
        or item.context.get("currently_resolving") is not True or item.targets
        or type(effect.get("schema_version")) is not int or effect not in combat_receiving_life_effects()
        or not combat_receiving_life_runtime_dependencies(program)
    ):
        raise ValueError("Combat receiving life requires its original resolving trigger")
    receipt = CombatReceivingLifeReceipt.from_dict(item.context.get(COMBAT_RECEIVING_LIFE_CONTEXT))
    if (
        not receipt.matches(item.context.get("event"), item.context)
        or item.source_object_id != item.context.get("target_object_id")
        or receipt.attacking_player not in host.state.players
    ):
        raise ValueError("Combat receiving life receipt disagrees with the original damage event")
    if effect[COMBAT_RECEIVING_LIFE_FIELD] == "controller_gain":
        return {"op": "life", "player": item.controller, "delta": receipt.amount}
    if receipt.attacking_player in host.active_seats:
        return {"op": "lose_life", "player": receipt.attacking_player, "amount": receipt.amount}
    return None
