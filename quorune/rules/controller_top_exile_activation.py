"""Validate the fixed ordinary activation that exiles its controller's current top."""

from collections.abc import Mapping

from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects
from .owner_library_position_shapes import has_exact_tap_price


CONTROLLER_TOP_EXILE_CAPABILITY = "activation.resolution.controller_top_exile"
CONTROLLER_TOP_EXILE_MECHANIC = "activation-controller-top-exile"
_TOP_EXILE_OPERATION = "exile_top_library_card"


def controller_top_exile_effect():
    return {"op": _TOP_EXILE_OPERATION, "player": "$controller"}


def _has_exact_tap_and_life_price(cost):
    if not isinstance(cost, Mapping):
        return False
    text = cost.get("text")
    life = cost.get("life_payment")
    return (
        isinstance(text, str) and " ".join(text.casefold().split()) == "{t}, pay 1 life"
        and type(life) is int and life == 1
        and has_exact_tap_price({**cost, "text": "{T}", "life_payment": 0})
    )


def controller_top_exile_activation_dependencies(effects, target_schema, active_zone, cost_schema):
    if (
        active_zone != "battlefield" or target_schema is not None
        or not _has_exact_tap_and_life_price(cost_schema)
        or thaw_value(effects) != [controller_top_exile_effect()]
    ):
        return ()
    return (CONTROLLER_TOP_EXILE_CAPABILITY,)


def controller_top_exile_runtime_dependencies(program):
    # Legacy Ingest is a triggered occurrence with a different player operand.
    # Inspect effect data as well as declarations so omission cannot bypass binding.
    carries_result = any(
        effect.get("op") == _TOP_EXILE_OPERATION
        and (program.event == "activate" or effect.get("player") == "$controller")
        for effect in nested_effects((program.effects, program.handlers))
    )
    if not carries_result and CONTROLLER_TOP_EXILE_CAPABILITY not in program.capability_dependencies:
        return ()

    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

    required = controller_top_exile_activation_dependencies(
        program.effects, program.target_schema, program.active_zone, program.cost_schema,
    )
    if (
        not required or program.event != "activate" or program.event_condition is not None
        or program.trigger_limit is not None or program.provenance.get("granted_only")
        or len(program.handlers) != 1
    ):
        raise ValueError("Controller top exile requires the complete targetless paid activation")
    abilities = activated_abilities_from_descriptors(program.handlers)
    if len(abilities) != 1:
        raise ValueError("Controller top exile requires one typed activated ability")
    ability, = abilities
    if (
        ability.builtin_semantic_key != program.key or ability.zones != ("battlefield",)
        or ability.mana_ability or ability.sorcery_speed or ability.activation_conditions
        or ability.target_schema is not None or activated_ability_cost(ability) != thaw_value(program.cost_schema)
    ):
        raise ValueError("Controller top exile lost its ordinary catalog price or scope")
    return required


def is_closed_controller_top_exile_program(program):
    try:
        required = controller_top_exile_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
