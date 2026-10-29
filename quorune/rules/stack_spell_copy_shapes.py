from __future__ import annotations

"""Exact typed source shape for the existing stack-copy choice owner."""

from ..stack_spell_copy_model import (
    FixedStackSpellCopySpec, STACK_SPELL_COPY_CAPABILITY, STACK_SPELL_COPY_MECHANIC,
)


def fixed_stack_spell_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..event_spell_copy_model import EVENT_SPELL_COPY_CAPABILITY,EVENT_SPELL_COPY_MECHANIC,event_spell_copy_effect
    if EVENT_SPELL_COPY_MECHANIC in set(mechanic_ids):
        return (EVENT_SPELL_COPY_CAPABILITY,) if any(tuple(effects)==(event_spell_copy_effect(retain_targets_only=retain),) for retain in (False,True)) and target_schema is None else ()
    if STACK_SPELL_COPY_MECHANIC not in set(mechanic_ids) or len(effects) != 1:
        return ()
    for controlled in (False, True):
        spec = FixedStackSpellCopySpec(controlled)
        if tuple(effects) == spec.effects and target_schema == spec.target_schema:
            return (STACK_SPELL_COPY_CAPABILITY, 'target.revalidate_resolution')
    return ()


def fixed_stack_spell_copy_program_is_closed(program):
    from ..card_programs.admission import program_has_declared_complete_source_admission
    if not program_has_declared_complete_source_admission(program):
        return False
    required = fixed_stack_spell_copy_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
