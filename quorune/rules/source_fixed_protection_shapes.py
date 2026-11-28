from __future__ import annotations

"""Minimum authority for a complete direct source protection bundle."""

from collections.abc import Mapping

from ..ability_fragments import ProtectionSpec, ability_fragment_from_dict
from ..compiler.source_fixed_protection import CAPABILITY, MECHANIC
from .temporary_ability_grant import (
    TEMPORARY_EACH_COLOR_PROTECTION_CAPABILITY, decode_temporary_ability_grant,
    temporary_quoted_grant_node_capabilities,
)


def source_fixed_protection_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1
            or not {MECHANIC, 'protection', 'cr-611-continuous-effects'} <= set(mechanic_ids)):
        return ()
    grant = effects[0]
    if not isinstance(grant, Mapping):
        return ()
    try:
        keywords, fragments = decode_temporary_ability_grant(grant)
        if (grant['op'] != 'apply_source_characteristics_until_end_of_turn'
                or grant['schema_version'] != 7 or grant['card'] != '$source.zone_object'
                or keywords or len(fragments) != 1 or grant['exclude_source'] or grant['printed_only']):
            return ()
        spec = ability_fragment_from_dict(fragments[0])
    except (TypeError, ValueError, KeyError):
        return ()
    if not isinstance(spec, ProtectionSpec) or spec.quality not in {'W', 'U', 'B', 'R', 'G'}:
        return ()
    return tuple(sorted((CAPABILITY, 'protection.typed.debt',
        'continuous.resolution.fixed_characteristics_until_end_of_turn')))


def _declares_each_color_protection(program):
    if TEMPORARY_EACH_COLOR_PROTECTION_CAPABILITY in program.capability_dependencies:
        return True
    if len(program.effects) != 1:
        return False
    effect, = program.effects
    fragments = effect.get("ability_fragments")
    return (effect.get("op") == "apply_source_characteristics_until_end_of_turn"
            and (effect.get("schema_version") == 8
                 or effect.get("schema_version") == 7 and effect.get("card") == "$target.0")
            and isinstance(fragments, (list, tuple))
            and any(isinstance(fragment, Mapping) and fragment.get("kind") == "protection" for fragment in fragments))


def source_fixed_protection_runtime_dependencies(program):
    if _declares_each_color_protection(program):
        required = temporary_quoted_grant_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        if (TEMPORARY_EACH_COLOR_PROTECTION_CAPABILITY not in required
                or (program.event, program.active_zone) != ("resolve", "stack") or program.handlers):
            raise ValueError("Protection from each color requires its closed resolving spell and recipient scope")
        return required
    marked = MECHANIC in program.coverage or any(
        effect.get('op') == 'apply_source_characteristics_until_end_of_turn'
        and effect.get('schema_version') == 7
        and isinstance(effect.get('ability_fragments'), (list, tuple))
        and any(isinstance(fragment, Mapping) and fragment.get('kind') == 'protection'
            for fragment in effect['ability_fragments']) for effect in program.effects)
    if not marked:
        return ()
    required = source_fixed_protection_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    if not required:
        raise ValueError('Fixed source protection requires the complete incarnation-bound color bundle')
    return required


def is_closed_source_fixed_protection_program(program):
    required = set(source_fixed_protection_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage))
    return bool(required) and required <= set(program.capability_dependencies)
