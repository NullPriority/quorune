from __future__ import annotations

"""Validated ability identity for subtype-specific activation cost scopes."""


def activation_matches_cost_scope(host,source,ability,scope):
    from dataclasses import replace
    if scope=='all':return True
    if scope=='nonmana':return not ability.mana_ability
    if scope=='equip':return ability.builtin_semantic_key=='builtin:equip'
    if scope!='cycling':raise ValueError('Unsupported activation cost ability selector')
    program=host.semantics.get(host._semantic_key_for_ability(source,ability))
    if program is None or not host.semantic_program_is_current_trusted(program):return False
    from ...semantic_runtime.cycling_abilities import cycling_specs_from_descriptors
    return any(replace(spec.to_activated_ability(),builtin_semantic_key=program.key)==ability
        for spec in cycling_specs_from_descriptors(program.handlers))
