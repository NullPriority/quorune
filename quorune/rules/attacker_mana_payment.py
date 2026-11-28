"""Resolve the payer and defending role for one original declared attacker."""

from collections.abc import Mapping

from ..attack_transition_model import AttackTransitionEvent
from ..replacement.immutable import thaw_value
from ..turn_history import current_turn_history_events


ATTACKER_MANA_PAYMENT_VERSION = 3
DEFENDED_ATTACK_VARIANT = "defended_opponent_attacker_payment"
DEFENDED_ATTACK_COVERAGE = "defended_opponent_attacker_intervening"
ATTACKER_PAYER_REFERENCE = "$event.attack_creature_controller"


def defended_attack_condition():
    return {"all": [
        {"field": "controller", "op": "ne", "value": "$source.controller"},
        {"field": "defending_player", "op": "eq", "value": "$source.controller"},
    ]}


def _original_attack(context):
    transition = AttackTransitionEvent.from_dict(thaw_value(context.get("attack_transition")))
    participant = next((row for row in transition.participants if row.reference == context.get("card")), None)
    assignment = next((row for row in transition.assignments
                       if participant is not None and row.attacker_object_id == participant.object_id), None)
    if (context.get("event_id") != transition.transition_id or assignment is None
            or participant.controller != context.get("controller")
            or assignment.recipient.reference != context.get("defender")
            or assignment.recipient.defending_player != context.get("defending_player")):
        raise ValueError("The defending condition requires the exact declared attacker and assignment")
    return participant, assignment


def defended_attack_recheck(context, controller):
    attacker, assignment = _original_attack(context)
    return assignment.recipient.defending_player == controller and attacker.controller != controller


def original_attacker_controller(host, item):
    participant, _assignment = _original_attack(item.context)
    card = host.state.cards.get(participant.object_id)
    if card is not None and card.zone == "battlefield" and card.logical_object_id == participant.logical_object_id:
        return card.controller
    history = host.state.turn_history
    if getattr(history, "departure_history_version", None) != 1:
        raise ValueError("The departed attacker requires the original controller departure journal")
    for entry in reversed(current_turn_history_events(history, turn_sequence=host.state.turn_sequence,
                                                      kind="permanent_left")):
        if entry.object_incarnation == participant.logical_object_id:
            return entry.actor
    raise ValueError("The departed attacker's original battlefield incarnation is missing")


def is_attacker_payment(effect):
    return (isinstance(effect, Mapping)
            and type(effect.get("opponent_event_mana_payment_version")) is int
            and effect["opponent_event_mana_payment_version"] == ATTACKER_MANA_PAYMENT_VERSION)


def attacker_token_capabilities(child, beneficiary):
    if (child.get("op") != "create_token" or child.get("controller") != beneficiary
            or type(child.get("quantity")) is not int or child["quantity"] != 1
            or set(child) != {"op", "controller", "name", "quantity", "characteristics"}):
        raise ValueError("Declining a declared attacker payment creates one ordinary token")
    attributes = child["characteristics"]
    if not isinstance(attributes, Mapping) or attributes.get("keywords") or attributes.get("ability_fragments"):
        raise ValueError("The declared attacker payment has an unsupported token definition")
    from .token_creation_capability_shapes import fixed_token_creation_node_capabilities, token_creation_component_mechanics

    token = {**thaw_value(child), "controller": "$controller"}
    capabilities = fixed_token_creation_node_capabilities(
        effects=(token,), target_schema=None,
        mechanic_ids=token_creation_component_mechanics((token,), {"cr-111-tokens"}),
    )
    if not capabilities:
        raise ValueError("The declined result lacks a complete typed token definition")
    return capabilities
