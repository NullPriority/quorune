"""Exact source-self graveyard entry followed by one independent player effect."""
from ..self_zone_move_followups import (
    FIELD, CAPABILITY, MECHANIC, decode_self_entry_followup,
    self_entry_followup_effect, self_entry_followup_payload_dependencies,
)


def self_entry_followup_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 2 or MECHANIC not in mechanic_ids:
        return ()
    try:
        followup = decode_self_entry_followup(effects[0])
    except (KeyError, TypeError, ValueError):
        return ()
    if effects[1] != self_entry_followup_effect(followup):
        return ()
    return tuple(sorted(self_entry_followup_payload_dependencies(effects[0])))


def self_entry_followup_runtime_dependencies(program):
    from .revealed_hand_zone_move import revealed_hand_entry_runtime_dependencies
    if required := revealed_hand_entry_runtime_dependencies(program):
        return required
    from ..self_zone_move import SELF_ZONE_MOVE_ABILITY_HANDLER_ID, SelfZoneMoveSpec
    from ..self_zone_move_followups import compiled_self_entry
    declared = []
    for descriptor in program.handlers:
        if descriptor.get('handler_id') == SELF_ZONE_MOVE_ABILITY_HANDLER_ID:
            spec = SelfZoneMoveSpec.from_dict(descriptor['move'])
            if spec.followup is not None:declared.append(spec)
    marked = [effect for effect in program.effects if FIELD in effect]
    if not marked and not declared:
        return ()
    if len(declared) != 1 or tuple(program.effects) != compiled_self_entry(declared[0])[1]:
        raise ValueError('Self-entry follow-up spec and exact instruction pair must agree')
    dependencies = self_entry_followup_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=(MECHANIC,))
    if (not dependencies or program.active_zone != 'graveyard' or program.event != 'activate'):
        raise ValueError('Self-entry follow-up requires its complete graveyard activation pair')
    from ..card_programs.admission import program_has_complete_card_program_admission
    if not program_has_complete_card_program_admission(program):
        raise ValueError('Self-entry follow-up requires complete original-card admission')
    return dependencies


def is_closed_self_entry_followup_program(program):
    required = self_entry_followup_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
