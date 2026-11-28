"""Bind a cast-or-attack observation to its original self-exile and owner return."""

from ..linked_exile_return_model import LINKED_EXILE_RETURN_CAPABILITY, LinkedExileReturnSpec
from ..replacement.immutable import FrozenMap
from .event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition
from .linked_exile_return_shapes import linked_exile_return_node_capabilities


CAPABILITY = "trigger.subscription.cast_or_attack_source_end_step_exile"
VARIANT = "cast_or_attack_source_end_step_exile"
TEMPLATE = "fixed-cast-or-attack-source-end-step-exile-v1"
DEPENDENCIES = (
    CAPABILITY, "trigger.subscription.fixed_multi_event", "trigger.event.normalized_spell_cast",
    "trigger.event.normalized_public_action", "trigger.effect.fixed_event", "trigger.placement.apnap",
    LINKED_EXILE_RETURN_CAPABILITY,
)


def cast_or_attack_source_exile_condition():
    return fixed_event_subscription_condition((
        FixedEventSubscription("spell.cast"),
        FixedEventSubscription("creature.attacks"),
    ))


def is_closed_next_end_step_self_exile(effects, target_schema, mechanics):
    if (target_schema is not None or len(effects) != 2 or effects[0].get("cards") != "$source.zone_object"
            or not linked_exile_return_node_capabilities(
                effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
            )):
        return False
    return effects[0]["spec"] == LinkedExileReturnSpec(timing="next_end_step", schema_version=1).to_dict()


def cast_or_attack_source_exile_runtime_dependencies(program):
    scope = FrozenMap(program.event_condition or {})
    expected = FrozenMap(cast_or_attack_source_exile_condition())
    if scope != expected and CAPABILITY not in program.capability_dependencies:
        return ()
    if (scope != expected or (program.event, program.active_zone) != ("spell.cast", "battlefield")
            or program.handlers or program.cost_schema is not None
            or not is_closed_next_end_step_self_exile(program.effects, program.target_schema, program.coverage)):
        raise ValueError("Cast or attack self-exile requires both original observations and its exact linked owner return")
    return DEPENDENCIES
