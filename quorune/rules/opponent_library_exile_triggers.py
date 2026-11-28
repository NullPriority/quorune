"""Closed trigger and effect dependencies for opposing library exile."""

from collections.abc import Mapping

from ..controller_creature_keyword_absence import is_controller_end_step_schedule
from ..opponent_library_exile_model import (
    EACH_OPPONENT_TOP_EXILE_CAPABILITY,
    EACH_OPPONENT_TOP_EXILE_OPERATION,
    DAMAGED_PLAYER_PRIVATE_TOP_CAPABILITY, DAMAGED_PLAYER_PRIVATE_TOP_VERSION, DAMAGED_PLAYER_PRIVATE_TOP_EVENT,
    damaged_player_private_top_condition,
    PRIVATE_TOP_EXILE_CAPABILITY, PRIVATE_TOP_EXILE_VERSION,
    ATTACKING_OPPONENT_TOP_EXILE_CAPABILITY, ATTACKING_OPPONENT_TOP_EXILE_VERSION,
    EXILE_INCARNATION_PLAY_CAPABILITY,
    OPPONENT_LIBRARY_EXILE_ACTOR_FIELD,
    OPPONENT_LIBRARY_EXILE_EVENT,
    OPPONENT_LIBRARY_EXILE_PLAY_CAPABILITY,
    OPPONENT_LIBRARY_EXILE_PLAY_OPERATION,
    each_opponent_top_exile_effect,
    validate_each_opponent_top_exile,
    validate_opponent_library_exile_play,
)
from ..replacement.immutable import FrozenMap
from ..resolving_library_exile import OPPONENT_LIBRARY_EXILE_EVENT_CAPABILITY
from .effect_shape_walkers import nested_effect_operations


EACH_OPPONENT_TOP_EXILE_MECHANIC = "fixed-each-opponent-library-top-exile"
DRAW_THEN_OPPONENT_EXILE_MECHANIC = "draw-then-exile-each-opponent-top"
DRAW_THEN_OPPONENT_EXILE_CAPABILITY = "library.exile.after_controller_draw"
OPPONENT_LIBRARY_EXILE_PLAY_MECHANIC = "fixed-controlled-opponent-library-exile-play"
OPPONENT_LIBRARY_EXILE_EVENT_MECHANIC = "trigger-event-resolving-opponent-library-exile"

_TOP_REQUIREMENTS = (
    EACH_OPPONENT_TOP_EXILE_CAPABILITY,
    "trigger.event.normalized_public_action",
    "trigger.placement.apnap",
    "zone.change.destination_replacement",
)
_PLAY_REQUIREMENTS = (
    OPPONENT_LIBRARY_EXILE_EVENT_CAPABILITY,
    OPPONENT_LIBRARY_EXILE_PLAY_CAPABILITY,
    "mana.spending.as_though",
    "trigger.placement.apnap",
)
_MECHANICS = {
    EACH_OPPONENT_TOP_EXILE_MECHANIC,
    OPPONENT_LIBRARY_EXILE_PLAY_MECHANIC,
    OPPONENT_LIBRARY_EXILE_EVENT_MECHANIC,
}
_TOP_CONDITION = {"field": "card", "op": "eq", "value": "$source.ref"}
_PLAY_CONDITION = {"field": OPPONENT_LIBRARY_EXILE_ACTOR_FIELD, "op": "eq", "value": "$source.controller"}


def draw_then_opponent_top_exile_effects():
    return (
        {"op": "draw", "player": "$controller", "count": 1, "private": True},
        each_opponent_top_exile_effect(),
    )


def _draw_then_exile_dependencies(effects, target_schema):
    expected_draw, expected_exile = draw_then_opponent_top_exile_effects()
    if (target_schema is not None or len(effects) != 2
            or FrozenMap(effects[0]) != FrozenMap(expected_draw)
            or type(effects[0].get("count")) is not int or effects[0].get("private") is not True
            or FrozenMap(effects[1]) != FrozenMap(expected_exile)
            or type(effects[1].get("schema_version")) is not int):
        return ()
    return (DRAW_THEN_OPPONENT_EXILE_CAPABILITY, "zone.draw.library_to_hand",
            EACH_OPPONENT_TOP_EXILE_CAPABILITY, "zone.change.destination_replacement")


def library_exile_trigger_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema):
    mechanics = set(mechanic_ids)
    if DRAW_THEN_OPPONENT_EXILE_MECHANIC in mechanics:
        return _draw_then_exile_dependencies(effects, target_schema)
    if _MECHANICS.isdisjoint(mechanics):
        return None
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    try:
        if mechanics.intersection(_MECHANICS) == {EACH_OPPONENT_TOP_EXILE_MECHANIC}:
            return _top_requirements(validate_each_opponent_top_exile(effects[0]), effects[0]["schema_version"])
        if mechanics.intersection(_MECHANICS) == {OPPONENT_LIBRARY_EXILE_EVENT_MECHANIC, OPPONENT_LIBRARY_EXILE_PLAY_MECHANIC}:
            validate_opponent_library_exile_play(effects[0])
            return _PLAY_REQUIREMENTS
    except (TypeError, ValueError):
        return ()
    return ()


def _top_requirements(grants_play, version):
    if version == DAMAGED_PLAYER_PRIVATE_TOP_VERSION:
        return (EACH_OPPONENT_TOP_EXILE_CAPABILITY, DAMAGED_PLAYER_PRIVATE_TOP_CAPABILITY,
                "trigger.event.normalized_damage", "attachment.reference.current_or_lki",
                "trigger.placement.apnap", "zone.change.destination_replacement", "mana.spending.as_though")
    if version == PRIVATE_TOP_EXILE_VERSION:
        from ..mutate_model import MUTATE_CAPABILITY

        return (*_TOP_REQUIREMENTS, PRIVATE_TOP_EXILE_CAPABILITY, MUTATE_CAPABILITY)
    required = (*_TOP_REQUIREMENTS, EXILE_INCARNATION_PLAY_CAPABILITY, "mana.spending.as_though") if grants_play else _TOP_REQUIREMENTS
    return (*required, ATTACKING_OPPONENT_TOP_EXILE_CAPABILITY) if version == ATTACKING_OPPONENT_TOP_EXILE_VERSION else required


def _activated_draw_then_exile_dependencies(program):
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    required = _draw_then_exile_dependencies(program.effects, program.target_schema)
    if (not required or (program.event, program.active_zone) != ("activate", "battlefield")
            or not isinstance(program.cost_schema, Mapping) or len(program.handlers) != 1
            or program.event_condition is not None or program.trigger_limit is not None):
        raise ValueError("Draw then opposing top exile requires its complete original battlefield activation")
    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if ability.builtin_semantic_key != program.key or ability.mana_ability:
        raise ValueError("Draw then opposing top exile requires its original paid ability")
    return required


def is_closed_draw_then_opponent_exile_program(program):
    if DRAW_THEN_OPPONENT_EXILE_CAPABILITY not in program.capability_dependencies:
        return False
    try:
        required = _activated_draw_then_exile_dependencies(program)
    except (TypeError, ValueError):
        return False
    return set(required) <= set(program.capability_dependencies)


def library_exile_trigger_runtime_dependencies(program):
    operations = nested_effect_operations(program.effects)
    top = EACH_OPPONENT_TOP_EXILE_OPERATION in operations
    play = OPPONENT_LIBRARY_EXILE_PLAY_OPERATION in operations or program.event == OPPONENT_LIBRARY_EXILE_EVENT
    activated_top = top and program.event == "activate"
    if activated_top or DRAW_THEN_OPPONENT_EXILE_CAPABILITY in program.capability_dependencies:
        return _activated_draw_then_exile_dependencies(program)
    from .attacking_opponent_library_exile import ATTACKED_PLAYER_OR_WALKER_EVENT, validate_attack_top_exile_program

    if program.event == ATTACKED_PLAYER_OR_WALKER_EVENT and (
        not top or len(program.effects) != 1 or program.effects[0].get("schema_version") != ATTACKING_OPPONENT_TOP_EXILE_VERSION
    ):
        raise ValueError("The grouped defending-player declaration requires its fixed top-exile result.")
    if not top and not play:
        return ()
    if (
        top == play or len(program.effects) != 1 or program.active_zone != "battlefield"
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or program.trigger_limit is not None
    ):
        raise ValueError("Opposing-library effects require one ordinary untargeted battlefield trigger.")
    if top:
        grants_play = validate_each_opponent_top_exile(program.effects[0])
        version = program.effects[0]["schema_version"]
        if version == PRIVATE_TOP_EXILE_VERSION:
            from ..mutate_model import MUTATE_EVENT

            if program.event != MUTATE_EVENT + ".self" or program.event_condition is not None:
                raise ValueError("Private opponent-library exile requires its original self mutation.")
        elif version == DAMAGED_PLAYER_PRIVATE_TOP_VERSION:
            if (program.event != DAMAGED_PLAYER_PRIVATE_TOP_EVENT
                    or FrozenMap(program.event_condition or {}) != FrozenMap(damaged_player_private_top_condition())):
                raise ValueError("Private damage exile requires its equipped creature's combat damage to a player")
        elif version == ATTACKING_OPPONENT_TOP_EXILE_VERSION:
            validate_attack_top_exile_program(program)
        elif grants_play:
            if program.event != "step.begin" or not is_controller_end_step_schedule(program.event_condition):
                raise ValueError("Incarnation-scoped top exile and play requires its controller's end step.")
        elif (program.event, program.event_condition) != ("creature.attacks", _TOP_CONDITION):
            raise ValueError("Opposing-library top exile requires its source creature's attack.")
        return _top_requirements(grants_play, version)
    if (program.event, program.event_condition) != (OPPONENT_LIBRARY_EXILE_EVENT, _PLAY_CONDITION):
        raise ValueError("Exile access requires a committed trigger controlled by the resolving effect's controller.")
    validate_opponent_library_exile_play(program.effects[0])
    return _PLAY_REQUIREMENTS
