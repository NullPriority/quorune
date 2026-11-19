from __future__ import annotations

"""Validated ability identity for subtype-specific activation cost scopes."""


def _catalogued_equip_matches(host, source, ability):
    """An opaque granted program key retains its independently typed keyword."""
    from dataclasses import replace
    from ...compiler.activated_costs import activated_ability_cost
    from ...replacement.immutable import thaw_value
    from ...semantic_runtime.activated_abilities import activated_abilities_from_descriptors
    program = host.semantics.get(host._semantic_key_for_ability(source, ability))
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or program.provenance.get('granted_only') is not True
            or program.event != 'activate' or program.active_zone != 'battlefield'
            or len(program.handlers) != 1 or thaw_value(program.effects) != [{
                'op':'attach','attachment_kind':'equipment',
                'source':'$source.zone_object','target':'$target.0'}]):
        return False
    for canonical in activated_abilities_from_descriptors(program.handlers):
        if canonical.builtin_semantic_key != 'builtin:equip':
            continue
        if (thaw_value(program.cost_schema) != activated_ability_cost(canonical)
                or thaw_value(program.target_schema) != thaw_value(canonical.target_schema)
                or ability.target_schema is not None and ability.target_schema != canonical.target_schema):
            continue
        # IDs and display text belong to the recipient's grant occurrence.
        # Operational costs, timing, targets and result remain canonical.
        expected = replace(canonical, ability_id=ability.ability_id, line_index=ability.line_index,
            oracle_line=ability.oracle_line, effect_text=ability.effect_text,
            builtin_semantic_key=ability.builtin_semantic_key, target_schema=ability.target_schema)
        if expected == ability:
            return True
    return False


def activation_matches_cost_scope(host,source,ability,scope):
    from dataclasses import replace
    if scope=='all':return True
    if scope=='nonmana':return not ability.mana_ability
    if scope=='equip':return ability.builtin_semantic_key=='builtin:equip' or _catalogued_equip_matches(host,source,ability)
    if scope=='boast':
        from ...boast import validate_boast_ability
        validate_boast_ability(ability)
        return ability.boast
    if scope!='cycling':raise ValueError('Unsupported activation cost ability selector')
    program=host.semantics.get(host._semantic_key_for_ability(source,ability))
    if program is None or not host.semantic_program_is_current_trusted(program):return False
    from ...semantic_runtime.cycling_abilities import cycling_specs_from_descriptors
    return any(replace(spec.to_activated_ability(),builtin_semantic_key=program.key)==ability
        for spec in cycling_specs_from_descriptors(program.handlers))
