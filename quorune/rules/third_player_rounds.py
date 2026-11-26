"""Exact independently rounded current thirds through the ordinary player rounds."""

from collections.abc import Mapping

from ..third_quantity_model import THIRD_LIFE_CAPABILITY, THIRD_QUANTITY_KIND, ThirdQuantitySpec
from .effect_shape_walkers import nested_effects
from .fixed_player_rounds import PLAYER_ROUND_SLOTS, validate_fixed_player_rounds


THIRD_PLAYER_ROUNDS_CAPABILITY = "resolution.effect_program.rounded_third_player_rounds"
_ROUNDED_UP = ThirdQuantitySpec("up")


def is_third_player_round_choice(effect):
    if not isinstance(effect, Mapping) or effect.get("op") != "choose_cards_apnap":
        return False
    count = effect.get("count")
    return isinstance(count, Mapping) and count.get("kind") == THIRD_QUANTITY_KIND


def third_player_round_effects(fixed):
    validate_fixed_player_rounds(fixed)
    return tuple({**effect, slot: _ROUNDED_UP.to_dict()}
                 for effect, slot in zip(fixed, PLAYER_ROUND_SLOTS, strict=True))


def third_player_rounds_node_capabilities(effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != len(PLAYER_ROUND_SLOTS):
        return ()
    projected = []
    try:
        for effect, slot in zip(effects, PLAYER_ROUND_SLOTS, strict=True):
            if not isinstance(effect, Mapping) or ThirdQuantitySpec.from_dict(effect[slot]) != _ROUNDED_UP:
                return ()
            projected.append({**effect, slot: 1})
        validate_fixed_player_rounds(projected)
    except (KeyError, TypeError, ValueError):
        return ()
    from .bound_effect_program_shapes import bound_effect_program_node_capabilities

    owned = bound_effect_program_node_capabilities(
        effects=tuple(projected), target_schema=None, mechanic_ids=mechanic_ids,
    )
    return (*owned, THIRD_LIFE_CAPABILITY, THIRD_PLAYER_ROUNDS_CAPABILITY) if owned else ()


def third_player_rounds_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if is_third_player_round_choice(effect))
    if not marked and THIRD_PLAYER_ROUNDS_CAPABILITY not in program.capability_dependencies:
        return ()
    if (program.event != "resolve" or program.active_zone != "stack" or program.handlers
            or tuple(program.effects[1:]) != marked):
        raise ValueError("Rounded-third choices require their original complete spell resolution")
    owned = third_player_rounds_node_capabilities(program.effects, program.target_schema, program.coverage)
    if not owned:
        raise ValueError("Rounded-third choices require the exact loss, discard and two sacrifice rounds")
    return owned
