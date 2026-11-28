"""Nontargeted control of the original artifact from a committed tap mana activation."""

from __future__ import annotations

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousEffectDuration, ContinuousObjectIdentity
from ..replacement.immutable import FrozenMap
from ..triggered_mana_model import SOURCE_MANA_TAP_CAPABILITY, SOURCE_MANA_TAP_EVENT
from .effect_shape_walkers import nested_effects


FIELD = "artifact_mana_tap_control"
ARTIFACT_TAP_FACT = "artifact_mana_tap"
ARTIFACT_TAP_ONLY = "artifact_mana_tap_only"
MANA_SOURCE_OBJECT_ID = "mana_source_object_id"
EVENT_CAPABILITY = "trigger.event.opponent_artifact_tap_for_mana"
CLEANUP_CONTROL_CAPABILITY = "continuous.control.until_controller_next_cleanup"
MECHANIC = "opponent-artifact-mana-tap-control"
VARIANT = "opponent_artifact_mana_tap_control"
TEMPLATE = "opponent-artifact-mana-tap-control-v1"
DURATION = ContinuousEffectDuration.UNTIL_END_OF_NEXT_TURN
_REQUIRES = (EVENT_CAPABILITY, SOURCE_MANA_TAP_CAPABILITY, CLEANUP_CONTROL_CAPABILITY,
             "continuous.control.fixed_resolution", "trigger.event.normalized_public_action", "trigger.placement.apnap")


def artifact_mana_tap_control_condition():
    return {"all": [
        {"field": "player", "op": "ne", "value": "$source.controller"},
        {"field": ARTIFACT_TAP_FACT, "op": "eq", "value": True},
    ]}


def artifact_mana_tap_control_effect():
    return {"op": "gain_control", "controller": "$controller", "duration": DURATION.value, FIELD: 1}


def uses_artifact_mana_tap_control(effect):
    return isinstance(effect, Mapping) and FIELD in effect


def artifact_mana_tap_control_payload_dependencies(effect):
    if not uses_artifact_mana_tap_control(effect):
        return ()
    if type(effect[FIELD]) is not int or FrozenMap(effect) != FrozenMap(artifact_mana_tap_control_effect()):
        raise ValueError("The artifact mana-tap control requires its exact original occurrence instruction")
    return _REQUIRES


def artifact_mana_tap_control_node_capabilities(effects, target_schema, cost_schema):
    if not any(uses_artifact_mana_tap_control(effect) for effect in effects):
        return None
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    try:
        return artifact_mana_tap_control_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def artifact_mana_tap_control_program_dependencies(program):
    if not any(uses_artifact_mana_tap_control(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = artifact_mana_tap_control_node_capabilities(program.effects, program.target_schema, program.cost_schema)
    if (not required or (program.event, program.active_zone) != (SOURCE_MANA_TAP_EVENT, "battlefield")
            or program.handlers or program.trigger_limit is not None or MECHANIC not in program.coverage
            or FrozenMap(program.event_condition or {}) != FrozenMap(artifact_mana_tap_control_condition())):
        raise ValueError("Artifact mana-tap control requires the original opposing artifact activation event")
    return required


def bind_artifact_mana_tap_control(state, item, effect):
    artifact_mana_tap_control_payload_dependencies(effect)
    event = item.context
    player = event.get("player")
    if (item.kind != "triggered_ability" or event.get("event") != SOURCE_MANA_TAP_EVENT
            or event.get(ARTIFACT_TAP_FACT) is not True or type(player) is not str or player not in state.players
            or type(event.get("card")) is not str or not event["card"]):
        raise ValueError("Artifact control lost the actual tap mana occurrence")
    identity = ContinuousObjectIdentity(event.get(MANA_SOURCE_OBJECT_ID), event.get("card_object_identity"))
    card = state.cards.get(identity.object_id)
    if (card is None or card.zone != "battlefield" or card.phased_out
            or card.logical_object_id != identity.logical_object_id or card.ref != event["card"]):
        return None
    return {**effect, "controller": item.controller, "card": card.ref, FIELD: identity.to_dict()}


def bound_artifact_mana_control_identity(effect, actor):
    base = {"op": "gain_control", "controller": actor, "duration": DURATION.value}
    if (set(effect) != {*base, "card", FIELD} or any(effect.get(key) != value for key, value in base.items())
            or type(effect["card"]) is not str or not effect["card"] or effect["card"].startswith("$")):
        raise ValueError("The bound artifact control lost its controller, duration or original recipient")
    return ContinuousObjectIdentity.from_dict(effect[FIELD])
