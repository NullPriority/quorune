from __future__ import annotations

"""Read-only typed result-source information prepared by trigger orchestration."""

from ..creature_power_damage import capture_creature_power_damage_source_lki
from ..creature_power_damage_model import CREATURE_POWER_DAMAGE_LKI_CONTEXT


def trigger_result_source_information(host, *, source, program, event, stack_context,
        source_characteristics, active_zone):
    """Collect typed result-source information after the occurrence has been sealed."""
    result = {}
    from ..phasing_model import phase_out_effect
    if tuple(program.effects) == (phase_out_effect(),):
        result["source_logical_object_id"] = source.logical_object_id
    from .gravestorm import GRAVESTORM_FIELD, gravestorm_program_dependencies, capture_gravestorm_cast_source
    if any(GRAVESTORM_FIELD in effect for effect in program.effects):
        if event != 'spell.cast' or active_zone != 'stack':
            raise ValueError('Gravestorm requires its source spell cast')
        gravestorm_program_dependencies(program)
        from ..event_spell_copy_model import EVENT_SPELL_COPY_CONTEXT
        result[EVENT_SPELL_COPY_CONTEXT] = capture_gravestorm_cast_source(host,source,stack_context).to_dict()
    from .demonstrate import DEMONSTRATE_FIELD, demonstrate_program_dependencies, capture_demonstrate_cast_source
    if any(DEMONSTRATE_FIELD in effect for effect in program.effects):
        if event != 'spell.cast' or active_zone != 'stack':
            raise ValueError('Demonstrate requires its source spell cast')
        demonstrate_program_dependencies(program)
        from ..event_spell_copy_model import EVENT_SPELL_COPY_CONTEXT
        result[EVENT_SPELL_COPY_CONTEXT] = capture_demonstrate_cast_source(host,source,stack_context).to_dict()
    from .ripple import RIPPLE_FIELD,RIPPLE_CAST_CONTEXT,ripple_program_dependencies,capture_ripple_cast_snapshot
    if any(RIPPLE_FIELD in effect for effect in program.effects):
        if event!='spell.cast' or active_zone!='stack':raise ValueError('Ripple source capture requires its actual spell cast')
        ripple_program_dependencies(program)
        result[RIPPLE_CAST_CONTEXT]=capture_ripple_cast_snapshot(host,source,stack_context,
            characteristics=source_characteristics).to_dict()
    source_lki = capture_creature_power_damage_source_lki(
        host,
        source,
        program.effects,
        characteristics=source_characteristics,
    )
    if source_lki is not None:
        result[CREATURE_POWER_DAMAGE_LKI_CONTEXT] = source_lki
    from ..scalar_effect_amounts import scalar_source_context
    result.update(scalar_source_context(host, source, program.effects, characteristics=source_characteristics))
    from ..target_source import capture_targeting_source, TARGETING_SOURCE_CONTEXT
    from ..ability_source_information import complete_ability_source_information
    from .endure_model import ENDURE_CAPABILITY
    if program.target_schema or complete_ability_source_information(host.state) or ENDURE_CAPABILITY in program.capability_dependencies:
        result[TARGETING_SOURCE_CONTEXT] = capture_targeting_source(
            host, source, characteristics=source_characteristics, zone=active_zone,
        ).to_dict()
    from .stack_damage_sources import fixed_damage_source_context
    result.update(fixed_damage_source_context(host, source, (program.effects, program.target_schema), characteristics=source_characteristics))
    from .event_damage_sources import event_damage_source_context
    result.update(event_damage_source_context(host, program, event, {**stack_context, **result}))
    from ..token_copy_references import copy_source_context
    result.update(copy_source_context(host, source, program.effects))
    return result
