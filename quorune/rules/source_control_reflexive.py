from __future__ import annotations

"""Commit original-source custody before placing its separate reflexive trigger."""

from collections.abc import Mapping

from ..recipient_control_model import RECIPIENT_CONTROL_CAPABILITY, GIVE_CONTROL_OPERATION
from ..reflexive_trigger_model import BOUND_REFLEXIVE_PROGRAM_VERSION, ReflexiveTriggerProgram
from ..replacement.immutable import FrozenMap, thaw_value

CONTROL_REFLEXIVE_CAPABILITY = "continuous.control.source_recipient_reflexive"
CONTROL_REFLEXIVE_FIELD = "source_control_reflexive"
CONTROL_REFLEXIVE_VERSION = 5


def source_control_reflexive_target():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}


def source_control_reflexive_child():
    return ReflexiveTriggerProgram(
        effects=(
            {"op": "draw", "player": "$controller", "count": 2, "private": True},
            {"op": "place_counters", "card": "$source.zone_object", "counter": "quest", "amount": 1,
             "source": "$source"},
        ),
        target_schema=None,
        mechanic_ids=("cr-121-drawing-a-card", "cr-122-counters"),
        schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION,
    )


def source_control_reflexive_effect():
    return {"op": GIVE_CONTROL_OPERATION, "schema_version": CONTROL_REFLEXIVE_VERSION,
            "card": "$source.zone_object", "controller": "$target.0", "source": "$source",
            CONTROL_REFLEXIVE_FIELD: source_control_reflexive_child().to_dict()}


def source_control_reflexive_parts(effect, *, resolved=False, allow_reason=False):
    expected = source_control_reflexive_effect()
    if resolved:
        for field in ("card", "controller", "source"):
            ref = effect.get(field)
            if ref is None and field != "source":
                expected[field] = None
            elif type(ref) is str and ref and not ref.startswith("$"):
                expected[field] = ref
            else:
                raise ValueError("Reflexive control requires its bound source and opponent")
    actual = dict(effect)
    if allow_reason and "reason" in actual:
        reason = actual.pop("reason")
        if type(reason) is not str or not reason:
            raise ValueError("Reflexive control requires a nonempty reason")
    child = effect.get(CONTROL_REFLEXIVE_FIELD)
    if (not isinstance(child, Mapping)
            or type(effect.get("schema_version")) is not int
            or type(child.get("schema_version")) is not int
            or FrozenMap(actual) != FrozenMap(expected)):
        raise ValueError("Reflexive control requires its complete original subject and consequence")
    consequence = ReflexiveTriggerProgram.from_dict(child)
    if consequence.effects[0].get("private") is not True:
        raise ValueError("Reflexive control must retain private draw authority")
    return consequence


def source_control_reflexive_dependencies(effect, target_schema):
    child = source_control_reflexive_parts(effect)
    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != FrozenMap(source_control_reflexive_target())):
        raise ValueError("Reflexive control requires one opposing player target")
    from .counter_placement_capability_shapes import fixed_counter_placement_node_capabilities

    counter_dependencies = fixed_counter_placement_node_capabilities(
        effects=(child.effects[1],), target_schema=None, mechanic_ids={"cr-122-counters"},
    )
    if not counter_dependencies:
        raise ValueError("Reflexive control lost its canonical counter placement")
    return (CONTROL_REFLEXIVE_CAPABILITY, RECIPIENT_CONTROL_CAPABILITY,
            "continuous.control.fixed_resolution", "target.revalidate_resolution",
            "trigger.reflexive.fixed_action_result", "trigger.placement.apnap", "zone.draw.library_to_hand",
            *counter_dependencies)


def validate_source_control_reflexive_program(program):
    from ..activation_condition_model import ActivationCondition, ActivationConditionKind
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    if (len(program.effects) != 1 or CONTROL_REFLEXIVE_FIELD not in program.effects[0]
            or program.event != "activate" or program.active_zone != "battlefield"
            or len(program.handlers) != 1
            or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Reflexive control requires the original battlefield activation")
    ability, = activated_abilities_from_descriptors(program.handlers)
    cost = program.cost_schema or {}
    if (cost.get("text") != "{T}" or cost.get("tap_source") is not True
            or any(cost.get("mana", {}).values())
            or any(cost.get(key) for key in (
                "complex_symbols", "untap_source", "discard_source", "sacrifice_source",
                "exile_source", "life_payment", "energy_payment", "loyalty_delta",
                "source_counter_removal_cost", "choices", "uncompiled_costs",
            ))
            or activated_ability_cost(ability) != thaw_value(cost)
            or ability.mana_ability or ability.sorcery_speed or ability.activation_limit is not None
            or ability.activation_conditions != (ActivationCondition(ActivationConditionKind.CONTROLLERS_TURN),)):
        raise ValueError("Reflexive control requires its original own-turn tap price")
    return source_control_reflexive_dependencies(program.effects[0], program.target_schema)


def commit_source_control_reflexive(host, intent):
    from ..control_effects import ControlEffectError, _current_duration_source, gain_control_of_refs
    from .reflexive_triggers import enqueue_resolving_reflexive

    parent = next((item for item in host.state.stack if item.ref == intent.source.stack_ref), None)
    if (parent is None or parent.controller != intent.actor or parent.kind != "activated_ability"
            or parent.context.get("currently_resolving") is not True
            or parent.source_object_id != intent.source.object_id
            or parent.context.get("source_logical_object_id") != intent.source.logical_object_id
            or tuple(parent.targets) != (intent.controller,)):
        raise ControlEffectError("Reflexive control lost its resolving activation or opponent")
    program = host.semantics.get(parent.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise ControlEffectError("Reflexive control requires its admitted activation")
    try:
        required = validate_source_control_reflexive_program(program)
    except (TypeError, ValueError, KeyError) as exc:
        raise ControlEffectError(str(exc)) from exc
    if not set(required) <= set(program.capability_dependencies):
        raise ControlEffectError("Reflexive control lost its committed result capability")

    card = _current_duration_source(host.state, intent.source)
    if card is None or card.ref != intent.object_ref or intent.controller not in host.active_seats:
        return ()
    previous = card.controller
    refs = gain_control_of_refs(
        host, actor=intent.actor, object_refs=(card.ref,), controller=intent.controller,
        duration=intent.duration, source=intent.source, reason=intent.reason,
    )
    current = _current_duration_source(host.state, intent.source)
    if (refs and current is not None and previous != intent.controller and current.controller == intent.controller):
        enqueue_resolving_reflexive(host, parent, source_control_reflexive_child())
    return refs
