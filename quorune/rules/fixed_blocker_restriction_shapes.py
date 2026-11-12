from __future__ import annotations

"""Independent compiler and runtime minimum for fixed blocker rule descriptors."""

from typing import Mapping
from ..fixed_blocker_restrictions import CAPABILITY, FixedBlockerRestrictionSpec
from ..source_block_pair_model import SOURCE_BLOCK_PAIR_KIND
from ..temporary_all_blocker_model import TEMPORARY_ALL_BLOCKER_KIND
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities


def _fixed_blocker_reference(effect):
    if (set(effect) != {'op', 'card', 'restriction'}
            or effect.get('op') != 'grant_declaration_restriction_until_end_of_turn'
            or effect.get('card') not in ('$target.0', '$source.zone_object')):
        raise ValueError('Fixed blocker rules require a direct target or the original battlefield source')
    FixedBlockerRestrictionSpec.from_dict(effect.get('restriction'))
    return effect['card']


def fixed_blocker_restriction_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1:
        return ()
    try:
        reference = _fixed_blocker_reference(effects[0])
    except (TypeError, ValueError):
        return ()
    required_mechanics = {'cr-611-continuous-effects', 'cr-509-declare-blockers-step'}
    capabilities = (CAPABILITY, 'combat.declaration.typed_components',
                    'continuous.resolution.declaration_rules_until_end_of_turn')
    if reference == '$target.0':
        if not direct_permanent_target_schema_is_closed(target_schema):
            return ()
        if target_schema.get('count') != 1 or not (
            'creature' in target_schema.get('types_all', ()) or target_schema.get('types_any') == ['creature']
        ):
            return ()
        required_mechanics.add('cr-115-targets')
        capabilities = (*capabilities, 'target.revalidate_resolution',
                        *direct_target_predicate_capabilities(target_schema))
    elif target_schema is not None:
        return ()
    return capabilities if required_mechanics <= set(mechanic_ids) else ()


def fixed_blocker_runtime_dependencies(program):
    """Require ownership from persisted effects, independently of declarations."""
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if (value.get('op') == 'grant_declaration_restriction_until_end_of_turn'
                    and isinstance(value.get('restriction'), Mapping)
                    and value['restriction'].get('kind') not in {SOURCE_BLOCK_PAIR_KIND, TEMPORARY_ALL_BLOCKER_KIND}):
                _fixed_blocker_reference(value)
                required.add(CAPABILITY)
            for child in value.values(): visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value: visit(child)
    visit((program.effects, program.handlers))
    return tuple(sorted(required))
