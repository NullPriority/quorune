from __future__ import annotations

"""Independent compiler and runtime minimum for fixed blocker rule descriptors."""

from typing import Mapping
from ..fixed_blocker_restrictions import CAPABILITY, FixedBlockerRestrictionSpec
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities


def fixed_blocker_restriction_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1:
        return ()
    effect = effects[0]
    if (set(effect) != {'op', 'card', 'restriction'}
            or effect.get('op') != 'grant_declaration_restriction_until_end_of_turn'
            or effect.get('card') != '$target.0'):
        return ()
    try:
        FixedBlockerRestrictionSpec.from_dict(effect.get('restriction'))
    except (TypeError, ValueError):
        return ()
    if not direct_permanent_target_schema_is_closed(target_schema):
        return ()
    schema = dict(target_schema or {})
    if schema.get('count') != 1 or not ('creature' in schema.get('types_all', ()) or schema.get('types_any') == ['creature']):
        return ()
    if not {'cr-115-targets', 'cr-611-continuous-effects', 'cr-509-declare-blockers-step'} <= set(mechanic_ids):
        return ()
    return (CAPABILITY, 'combat.declaration.typed_components', 'continuous.resolution.declaration_rules_until_end_of_turn',
        'target.revalidate_resolution', *direct_target_predicate_capabilities(target_schema))


def fixed_blocker_runtime_dependencies(program):
    """Require ownership from persisted effects, independently of declarations."""
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if (value.get('op') == 'grant_declaration_restriction_until_end_of_turn'
                    and isinstance(value.get('restriction'), Mapping)):
                if set(value) != {'op', 'card', 'restriction'} or value.get('card') != '$target.0':
                    raise ValueError('Fixed blocker rules require one canonical direct target')
                FixedBlockerRestrictionSpec.from_dict(value['restriction'])
                required.add(CAPABILITY)
            for child in value.values(): visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value: visit(child)
    visit((program.effects, program.handlers))
    return tuple(sorted(required))
