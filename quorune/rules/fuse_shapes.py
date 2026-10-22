from __future__ import annotations

"""Closed two-body target binding and program correlation for Fuse."""

from collections.abc import Mapping
from ..semantics import SemanticProgram
from .fuse_model import FuseSpec, FUSE_CAPABILITY, FUSE_TEMPLATE, FUSE_BOUNDARY


def _map_tree(value, references):
    if isinstance(value, Mapping):
        if value.get('kind')=='scalar_effect_amount' and value.get('origin')=='target_player_hand_count':
            from ..scalar_effect_amount_model import ScalarEffectAmountSpec
            spec=ScalarEffectAmountSpec.from_dict(value)
            reference=references.get('$target.0','')
            if spec.schema_version!=4 or not reference.startswith('$target_group.') or not reference.endswith('.0'):
                raise ValueError('Fuse requires an exact half-local hand-count target')
            return {**value,'schema_version':7,'target_group':reference[len('$target_group.'):-2]}
        return {key: _map_tree(child, references) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_map_tree(child, references) for child in value]
    if isinstance(value, str):
        if value in references:
            return references[value]
        if value == '$targets' or value.startswith(('$target.', '$target_group.')):
            raise ValueError('Fuse body contains an unbound target reference')
    return value


def _half_target_binding(schema, offset):
    if schema is None:
        return [], {}, []
    from ..targets import target_plan
    if not isinstance(schema, Mapping) or 'modes' in schema:
        raise ValueError('Fuse requires closed nonmodal half targets')
    if 'groups' in schema and set(schema) - {'groups', 'globally_distinct', 'same_player_groups'}:
        raise ValueError('Fuse does not discard half-wide target constraints')
    plan = target_plan(schema)
    raw_groups = schema.get('groups', [schema])
    names = {g.group_id: f'target_{offset + index}' for index, g in enumerate(plan.groups)}
    groups, references = [], {}
    flat = 0
    for raw, group in zip(raw_groups, plan.groups, strict=True):
        if group.min_targets != 1 or group.max_targets != 1:
            raise ValueError('This Fuse production requires fixed single-object target roles')
        new_name = names[group.group_id]
        rebased = {**raw, 'id': new_name}
        if group.different_from_groups or plan.globally_distinct:
            rebased['different_from_groups'] = list(dict.fromkeys([
                *(names[key] for key in group.different_from_groups),
                *(names[g.group_id] for g in plan.groups if plan.globally_distinct and g is not group)]))
        groups.append(rebased)
        references[f'$target_group.{group.group_id}'] = f'$target_group.{new_name}'
        for index in range(group.max_targets):
            target = f'$target_group.{new_name}.{index}'
            references[f'$target.{flat}'] = target
            references[f'$target_group.{group.group_id}.{index}'] = target
            flat += 1
    if len(groups) == 1:
        references['$targets'] = '$target_group.' + groups[0]['id']
    same_player = [[names[a], names[b]] for a, b in plan.same_player_groups]
    return groups, references, same_player


def fused_body(halves):
    effects, groups, same_player = [], [], []
    if len(halves) != 2:
        raise ValueError('A fused spell requires exactly two half programs')
    for half in halves:
        half_groups, references, same = _half_target_binding(half.target_schema, len(groups))
        effects.extend(_map_tree(half.effects, references))
        groups.extend(half_groups)
        same_player.extend(same)
    schema = {'groups': groups} if groups else None
    if same_player:
        schema['same_player_groups'] = same_player
    return effects, schema


def closed_fused_program(program):
    if program.provenance.get('template_id') != FUSE_TEMPLATE:
        return False
    try:
        spec = FuseSpec.from_dict(program.provenance['fuse'])
        raw = program.provenance['fuse_components']
        if not isinstance(raw, list) or len(raw) != 2:
            return False
        halves = [SemanticProgram.from_dict(value) for value in raw]
        if program.provenance.get('fuse_source') != spec.to_dict():
            return False
        from ..compiler.program_generation import _is_closed_effect_program
        from ..card_programs.admission import program_has_declared_complete_source_admission
        for half, expected in zip(halves, (spec.left, spec.right), strict=True):
            if (half.ability_id != 'spell:' + expected.face_id or half.oracle_id != program.oracle_id
                    or half.key != f'{program.oracle_id}:{half.ability_id}' or half.handlers or half.cost_schema
                    or half.event_condition or half.destination != 'graveyard' or not half.effects
                    or half.provenance.get('template_id') == FUSE_TEMPLATE
                    or not program_has_declared_complete_source_admission(half)
                    or half.provenance.get('fuse_source') != spec.to_dict()
                    or not _is_closed_effect_program(half)):
                return False
            for field in ('source_oracle_hash', 'source_rulings_hash', 'semantic_hash'):
                if half.provenance.get(field) != program.provenance.get(field):
                    return False
        effects, schema = fused_body(halves)
        dependencies = {FUSE_CAPABILITY, *(c for half in halves for c in half.capability_dependencies)}
        return (program.ability_id == spec.ability_id and program.key == f'{program.oracle_id}:{spec.ability_id}'
            and program.effects == effects and program.target_schema == schema and not program.cost_schema
            and not program.handlers and not program.event_condition and program.destination == 'graveyard'
            and program.active_zone == 'stack' and program.event == 'resolve'
            and FUSE_BOUNDARY in program.coverage and 'fused_spell_resolution' in program.coverage
            and set(program.capability_dependencies) == dependencies
            and program_has_declared_complete_source_admission(program))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def fuse_runtime_requirements(program):
    """Revalidate the assembled body and delegate each restored half to its owners."""
    if (program.provenance.get('template_id') != FUSE_TEMPLATE
            and 'fused_spell_resolution' not in program.coverage and 'fuse_components' not in program.provenance):
        if 'fuse_source' in program.provenance:
            FuseSpec.from_dict(program.provenance['fuse_source'])
            return {FUSE_CAPABILITY}, set()
        return None
    if not closed_fused_program(program):
        return {FUSE_CAPABILITY}, {'runtime_fuse:invalid:complete ordered half programs are required'}
    from ..card_programs.runtime_requirements import program_dynamic_runtime_requirements
    required, blockers = {FUSE_CAPABILITY}, set()
    for raw in program.provenance['fuse_components']:
        child_required, child_blockers = program_dynamic_runtime_requirements(SemanticProgram.from_dict(raw))
        required.update(child_required)
        blockers.update(child_blockers)
    return required, blockers
