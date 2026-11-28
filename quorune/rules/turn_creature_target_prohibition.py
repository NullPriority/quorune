"""Compile and independently validate the original controller's targeting-rule grant."""

from ..compiler.spell_cast_predicates import FixedSpellCastController, FixedSpellCastQuality, FixedSpellCastSubject
from ..replacement.immutable import FrozenMap
from ..turn_creature_target_prohibition_model import (
    TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY, TURN_CREATURE_TARGET_PROHIBITION_MECHANIC,
    uses_turn_creature_target_prohibition, validate_turn_creature_target_prohibition,
)
from .effect_shape_walkers import nested_effects


CONTROLLER_SPELL_CAST_CONDITION = FrozenMap(FixedSpellCastSubject(
    FixedSpellCastController.SOURCE, quality=FixedSpellCastQuality.ANY,
).event_condition)


def turn_creature_target_prohibition_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1
            or TURN_CREATURE_TARGET_PROHIBITION_MECHANIC not in mechanic_ids):
        return ()
    try:
        validate_turn_creature_target_prohibition(effects[0])
    except (TypeError, ValueError):
        return ()
    return (TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY, "trigger.event.normalized_spell_cast", "trigger.placement.apnap")


def turn_creature_target_prohibition_program_dependencies(program):
    from .targeted_turn_target_prohibition import targeted_turn_prohibition_program_dependencies

    if targeted := targeted_turn_prohibition_program_dependencies(program):
        return targeted
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_turn_creature_target_prohibition(effect))
    if not marked and TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY not in program.capability_dependencies:
        return ()
    required = turn_creature_target_prohibition_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or marked != tuple(program.effects) or program.handlers or program.cost_schema is not None
            or (program.event, program.active_zone) != ("spell.cast", "battlefield")
            or FrozenMap(program.event_condition or {}) != CONTROLLER_SPELL_CAST_CONDITION):
        raise ValueError("The turn-long targeting prohibition requires the original controller's ordinary cast trigger")
    return required
