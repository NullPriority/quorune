"""Closed optional typed library reveal rounds with a public life sibling."""

from __future__ import annotations

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects
from .public_life_gain_scopes import PUBLIC_LIFE_GAIN_FIELD, validate_public_life_gain


FIELD = "apnap_library_reveals"
CAPABILITY = "library.select.apnap_optional_land_and_spell_reveals"
LOOK_COUNT = 5
LAND = "land"
SPELL = "instant_or_sorcery"
ROLE_ORDER = (LAND, SPELL)


def library_reveal_round_effects():
    return (
        {"op": "choose_cards_apnap", FIELD: 1},
        {"op": "life", PUBLIC_LIFE_GAIN_FIELD: 1, "scope": "all_active_players", "delta": 3},
    )


def uses_library_reveal_round(effect):
    return isinstance(effect, Mapping) and FIELD in effect


def library_reveal_round_payload_dependencies(effect):
    if not uses_library_reveal_round(effect):
        return ()
    if type(effect[FIELD]) is not int or FrozenMap(effect) != FrozenMap(library_reveal_round_effects()[0]):
        raise ValueError("A private library reveal round requires the exact closed look and reveal roles")
    return (CAPABILITY, "library.select.fixed_controller")


def library_reveal_round_node_capabilities(effects, target_schema, cost_schema):
    if not any(uses_library_reveal_round(effect) for effect in effects):
        return None
    if target_schema is not None or cost_schema is not None or len(effects) != 2:
        return ()
    if tuple(FrozenMap(effect) for effect in effects) != tuple(FrozenMap(effect) for effect in library_reveal_round_effects()):
        return ()
    try:
        return (*library_reveal_round_payload_dependencies(effects[0]), *validate_public_life_gain(effects[1]))
    except (TypeError, ValueError):
        return ()


def library_reveal_round_program_dependencies(program):
    if not any(uses_library_reveal_round(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = library_reveal_round_node_capabilities(program.effects, program.target_schema, program.cost_schema)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.handlers):
        raise ValueError("The private reveal round requires the complete untargeted spell and life sibling")
    return required


def is_closed_library_reveal_round_program(program):
    try:
        required = library_reveal_round_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
