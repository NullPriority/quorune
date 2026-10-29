from __future__ import annotations

"""Capability minima for resolved all-able-blocker requirements."""

from typing import Mapping

from ..temporary_all_blocker_model import (
    TEMPORARY_ALL_BLOCKER_CAPABILITY, TEMPORARY_ALL_BLOCKER_KIND, TemporaryAllBlockerSpec,
)
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed


def temporary_all_blocker_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1:
        return ()
    effect = effects[0]
    if set(effect) != {'op', 'card', 'restriction'} or effect.get('op') != 'grant_declaration_restriction_until_end_of_turn':
        return ()
    try:
        TemporaryAllBlockerSpec.from_dict(effect.get('restriction'))
    except (TypeError, ValueError):
        return ()
    targeted = effect.get('card') == '$target.0'
    if targeted:
        if (not direct_permanent_target_schema_is_closed(target_schema)
                or target_schema.get('count') != 1 or target_schema.get('types_any') != ['creature']):
            return ()
    elif effect.get('card') != '$source.zone_object' or target_schema is not None:
        return ()
    if not {'cr-611-continuous-effects', 'cr-509-declare-blockers-step',
            *(('cr-115-targets',) if targeted else ())} <= set(mechanic_ids):
        return ()
    return (TEMPORARY_ALL_BLOCKER_CAPABILITY, 'combat.declaration.typed_components',
        'continuous.resolution.declaration_rules_until_end_of_turn',
        *(('target.revalidate_resolution',) if targeted else ()))


def temporary_all_blocker_runtime_dependencies(program):
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            spec = value.get('restriction')
            if (value.get('op') == 'grant_declaration_restriction_until_end_of_turn'
                    and isinstance(spec, Mapping) and spec.get('kind') == TEMPORARY_ALL_BLOCKER_KIND):
                TemporaryAllBlockerSpec.from_dict(spec)
                if set(value) != {'op', 'card', 'restriction'} or value.get('card') not in {'$target.0', '$source.zone_object'}:
                    raise ValueError('Temporary all-blocker rules require one canonical recipient')
                required.update((TEMPORARY_ALL_BLOCKER_CAPABILITY, 'combat.declaration.typed_components',
                    'continuous.resolution.declaration_rules_until_end_of_turn'))
                if value['card'] == '$target.0':
                    required.add('target.revalidate_resolution')
            for child in value.values():
                visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                visit(child)
    visit((program.effects, program.handlers))
    return tuple(sorted(required))
