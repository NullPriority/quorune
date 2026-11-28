from __future__ import annotations

from collections.abc import Mapping

from ..discover_model import (DISCOVER_OPERATION, DISCOVER_TREASURE_DIFFERENCE_CAPABILITY,
    DISCOVER_TREASURE_DIFFERENCE_VERSION,
    NONLAND_SCAN_MECHANIC, NONLAND_SCAN_OPERATION,
    QUALIFIED_SCAN_MECHANIC, QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_TARGET, discover_payload_dependencies)
from ..replacement.immutable import FrozenMap
from .elected_nonland_exile import (
    ELECTED_NONLAND_EXILE_MECHANIC, ELECTED_NONLAND_EXILE_SCOPE, validate_elected_nonland_exile_program,
)
from ..land_exile_source_return_model import LAND_EXILE_RETURN_MECHANIC, LAND_EXILE_RETURN_SCOPE
from ..mana_value_exile_model import TOTAL_MANA_EXILE_SCOPE, TOTAL_MANA_EXILE_MECHANIC
from ..targeted_exile_land_entry import TARGETED_EXILE_LAND_ENTRY_MECHANIC, TARGETED_EXILE_LAND_ENTRY_SCOPE
from ..fixed_top_exile import (
    CHOSEN_IMPULSE_MECHANIC,
    CHOSEN_IMPULSE_SCOPE,
    FIXED_TOP_EXILE_CAPABILITY,
    FIXED_TOP_EXILE_MECHANIC,
    FIXED_TOP_EXILE_OPERATION,
    OWN_LIBRARY_EXILE_SCOPES,
    OPPONENT_CHOSEN_IMPULSE_SCOPE,
    RESOLVING_TOP_PLAY_MECHANIC,
    RESOLVING_TOP_PLAY_SCOPE,
    validate_fixed_top_exile,
)


def discover_node_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects) != 1 or not isinstance(effects[0], Mapping):
        return ()
    operation = effects[0].get('op')
    if type(operation) is not str:
        return ()
    mechanic = {DISCOVER_OPERATION: 'discover', NONLAND_SCAN_OPERATION: NONLAND_SCAN_MECHANIC,
                QUALIFIED_SCAN_OPERATION: QUALIFIED_SCAN_MECHANIC,
                FIXED_TOP_EXILE_OPERATION: FIXED_TOP_EXILE_MECHANIC}.get(operation)
    if operation == FIXED_TOP_EXILE_OPERATION:
        mechanic = {TOTAL_MANA_EXILE_SCOPE: TOTAL_MANA_EXILE_MECHANIC, CHOSEN_IMPULSE_SCOPE: CHOSEN_IMPULSE_MECHANIC,
                    OPPONENT_CHOSEN_IMPULSE_SCOPE: CHOSEN_IMPULSE_MECHANIC,
                    RESOLVING_TOP_PLAY_SCOPE: RESOLVING_TOP_PLAY_MECHANIC,
                    LAND_EXILE_RETURN_SCOPE: LAND_EXILE_RETURN_MECHANIC,
                    ELECTED_NONLAND_EXILE_SCOPE: ELECTED_NONLAND_EXILE_MECHANIC,
                    TARGETED_EXILE_LAND_ENTRY_SCOPE: TARGETED_EXILE_LAND_ENTRY_MECHANIC,
                    }.get(effects[0].get("scope"), mechanic)
    if mechanic is None or mechanic not in mechanic_ids:
        return ()
    try:
        required = discover_payload_dependencies(effects[0])
        if operation == QUALIFIED_SCAN_OPERATION and effects[0]['supplier'] == '$target.0':
            if not isinstance(target_schema, Mapping) or FrozenMap(target_schema) != QUALIFIED_SCAN_TARGET:
                return ()
            return (*required, 'target.revalidate_resolution')
        if operation == FIXED_TOP_EXILE_OPERATION and effects[0].get("scope") in {
            LAND_EXILE_RETURN_SCOPE, TARGETED_EXILE_LAND_ENTRY_SCOPE, OPPONENT_CHOSEN_IMPULSE_SCOPE,
        }:
            return required if _original_opponent_target(target_schema) else ()
        return required if target_schema is None else ()
    except (TypeError, ValueError, KeyError):
        return ()


def is_closed_discover_program(program):
    required=discover_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)


def validate_exile_scan_program_scope(program, values, dependencies):
    if FIXED_TOP_EXILE_CAPABILITY in dependencies:
        validate_fixed_top_exile_program_scope(program, values)
    if DISCOVER_TREASURE_DIFFERENCE_CAPABILITY in dependencies:
        validate_discover_treasure_program(program)


def validate_discover_treasure_program(program):
    if (program.event != 'resolve' or program.active_zone != 'stack'
            or not program.ability_id.startswith('spell:') or program.handlers
            or program.target_schema is not None or len(program.effects) != 1):
        raise ValueError('The Treasure difference requires one untargeted Discover spell')
    effect = program.effects[0]
    if (effect.get('op') != DISCOVER_OPERATION
            or effect.get('schema_version') != DISCOVER_TREASURE_DIFFERENCE_VERSION):
        raise ValueError('The Treasure difference requires its original Discover instruction')
    discover_payload_dependencies(effect)
    return effect['amount']


def _legacy_fixed_top_instructions(value):
    if isinstance(value, Mapping):
        if value.get("op") == FIXED_TOP_EXILE_OPERATION and validate_fixed_top_exile(value) not in OWN_LIBRARY_EXILE_SCOPES:
            yield value
        for child in value.values():
            yield from _legacy_fixed_top_instructions(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _legacy_fixed_top_instructions(child)


def _original_opponent_target(target_schema):
    return (isinstance(target_schema, Mapping) and type(target_schema.get("count")) is int
            and FrozenMap(target_schema) == QUALIFIED_SCAN_TARGET)


def original_source_cast_condition():
    from ..compiler.spell_cast_predicates import FixedSpellCastController, FixedSpellCastQuality, FixedSpellCastSubject

    return FrozenMap(FixedSpellCastSubject(
        FixedSpellCastController.SOURCE, FixedSpellCastQuality.ANY, source_spell=True,
    ).event_condition)


def _validate_targeted_exile_land_entry_program(program):
    if ((program.event, program.active_zone) != ("spell.cast", "stack")
            or program.handlers or program.cost_schema is not None
            or not _original_opponent_target(program.target_schema)
            or FrozenMap(program.event_condition or {}) != original_source_cast_condition()):
        raise ValueError("Opponent exile and land entry requires the original self-cast and opponent target")


def _validate_land_exile_return_program(program):
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    if ((program.event, program.active_zone) != ("activate", "battlefield")
            or not _original_opponent_target(program.target_schema) or len(program.handlers) != 1
            or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("The public land result requires its original targeted battlefield activation")
    abilities = activated_abilities_from_descriptors(program.handlers)
    if len(abilities) != 1 or program.ability_id != f"ability:{abilities[0].ability_id}":
        raise ValueError("The public land result lost its original activation identity")


def _validate_opponent_chosen_impulse_program(program, instruction):
    from .modal_capability_shapes import FIXED_CHOOSE_ONE_MODAL_MECHANIC, fixed_choose_one_modal_branches

    if ((program.event, program.active_zone) != ("resolve", "stack") or program.handlers
            or program.event_condition is not None or program.cost_schema is not None):
        raise ValueError("Opponent impulse access requires its original resolving spell")
    if tuple(program.effects) == (instruction,) and _original_opponent_target(program.target_schema):
        return
    schema = program.target_schema
    modes = schema.get("modes") if isinstance(schema, Mapping) else None
    if not isinstance(modes, Mapping) or FIXED_CHOOSE_ONE_MODAL_MECHANIC not in program.coverage:
        raise ValueError("Opponent impulse access requires the printed choose-one modal owner")
    mechanics = dict.fromkeys((FIXED_CHOOSE_ONE_MODAL_MECHANIC, *(
        entry for mode in modes.values() if isinstance(mode, Mapping)
        for entry in mode.get("mechanics", ()) if isinstance(entry, str)
    )))
    branches = fixed_choose_one_modal_branches(
        effects=program.effects, target_schema=schema, mechanic_ids=mechanics,
    )
    if (not program.effects and branches is not None and sum(
            branch.effects == (instruction,) and _original_opponent_target(branch.target_schema)
            for branch in branches) == 1):
        return
    raise ValueError("Opponent impulse access lost its original mode and opponent target")


def validate_fixed_top_exile_program_scope(program, values):
    legacy = tuple(_legacy_fixed_top_instructions(values))
    if not legacy:
        return
    if len(legacy) == 1 and validate_fixed_top_exile(legacy[0]) == OPPONENT_CHOSEN_IMPULSE_SCOPE:
        _validate_opponent_chosen_impulse_program(program, legacy[0])
        return
    if len(legacy) != 1 or len(program.effects) != 1 or legacy[0] != program.effects[0]:
        raise ValueError("Fixed top exile requires a single closed instruction")
    scope = validate_fixed_top_exile(legacy[0])
    if scope == ELECTED_NONLAND_EXILE_SCOPE:
        validate_elected_nonland_exile_program(program)
        return
    if scope == LAND_EXILE_RETURN_SCOPE:
        _validate_land_exile_return_program(program)
        return
    if scope == TARGETED_EXILE_LAND_ENTRY_SCOPE:
        _validate_targeted_exile_land_entry_program(program)
        return
    if program.target_schema is not None:
        raise ValueError("Fixed top resolving casts require one closed untargeted instruction")
    if scope == "cast_event_library" and program.event != "spell.cast":
        raise ValueError("The supplying caster must come from the normalized spell cast event")
