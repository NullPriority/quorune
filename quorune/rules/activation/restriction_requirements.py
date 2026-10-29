from __future__ import annotations

"""Descriptor-owned activation usage and turn-fact capability minima."""

from ...boast import boast_runtime_dependencies
from ...activation_usage import ActivationLimit,ACTIVATION_ONCE_PER_OBJECT_CAPABILITY
from ...activation_condition_model import ActivationConditionKind
from .turn_event_conditions import ACTIVATION_TURN_EVENT_CAPABILITY


def activation_restriction_runtime_dependencies(program):
    from ...semantic_runtime.activated_abilities import activated_abilities_from_descriptors,is_structural_activated_ability_catalog_program
    required=set(boast_runtime_dependencies(program))
    if is_structural_activated_ability_catalog_program(program):return tuple(sorted(required))
    for ability in activated_abilities_from_descriptors(program.handlers):
        if ability.forecast:
            from ...forecast_model import FORECAST_CAPABILITY
            required.add(FORECAST_CAPABILITY)
        if any(choice.fixed_tap_cost() is not None and choice.fixed_tap_cost().schema_version == 2 for choice in ability.choices):
            from ..activation_costs import COLORED_TAP_ACTIVATION_CAPABILITY
            required.add(COLORED_TAP_ACTIVATION_CAPABILITY)
        from .public_state_conditions import activation_public_state_dependencies
        required.update(activation_public_state_dependencies(ability.activation_conditions))
        if ability.activation_limit is ActivationLimit.ONCE_PER_OBJECT:required.add(ACTIVATION_ONCE_PER_OBJECT_CAPABILITY)
        if any(condition.kind in {ActivationConditionKind.CONTROLLER_CAST_INSTANT_SORCERY_THIS_TURN,
                ActivationConditionKind.OPPONENT_LOST_LIFE_THIS_TURN} for condition in ability.activation_conditions):
            required.add(ACTIVATION_TURN_EVENT_CAPABILITY)
    return tuple(sorted(required))
