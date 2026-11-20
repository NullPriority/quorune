"""Source-self entry-counter shape and admission authority."""
from ..source_self_entry_counter_model import (
    FIELD, MECHANIC, DEPENDENCIES, source_self_entry_counter_from_effect,
)


def source_self_entry_counter_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None or MECHANIC not in mechanic_ids:
        return ()
    try:source_self_entry_counter_from_effect(effects[0])
    except (ValueError, TypeError, KeyError):return ()
    return DEPENDENCIES


def source_self_entry_counter_runtime_dependencies(program):
    from ..self_zone_move import SELF_ZONE_MOVE_ABILITY_HANDLER_ID, SelfZoneMoveSpec
    declared = [SelfZoneMoveSpec.from_dict(descriptor['move']) for descriptor in program.handlers
                if descriptor.get('handler_id') == SELF_ZONE_MOVE_ABILITY_HANDLER_ID]
    declared = [spec for spec in declared if spec.entry_counter is not None]
    marked = [effect for effect in program.effects if FIELD in effect]
    if not declared and not marked:
        return ()
    if (len(declared) != 1 or tuple(program.effects) != (declared[0].effect(),)
            or program.event != 'activate' or program.active_zone != 'graveyard'
            or program.target_schema is not None):
        raise ValueError('Source entry counters require their complete activation and matching movement spec')
    from ..card_programs.admission import program_has_complete_card_program_admission
    if not program_has_complete_card_program_admission(program):
        raise ValueError('Source entry counters require complete original-card admission')
    source_self_entry_counter_from_effect(program.effects[0])
    return DEPENDENCIES


def is_closed_source_self_entry_counter_program(program):
    required = source_self_entry_counter_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
