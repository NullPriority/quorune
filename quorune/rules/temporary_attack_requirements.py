"""Minimum ownership and live projection of resolved attack requirements."""
from collections.abc import Mapping
from ..temporary_attack_requirement_model import (
    TEMPORARY_ATTACK_REQUIREMENT_KIND, TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY,
    TemporaryAttackRequirementSpec,
)

_MINIMUM = (TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY, 'combat.declaration.typed_components',
    'continuous.resolution.declaration_rules_until_end_of_turn', 'target.revalidate_resolution')
_SCHEMA = {'zones': ['battlefield'], 'categories': ['permanent'], 'types_any': ['creature'], 'count': 1}


def temporary_attack_requirement_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1: return ()
    effect = effects[0]
    if (set(effect) != {'op', 'card', 'restriction'}
            or effect.get('op') != 'grant_declaration_restriction_until_end_of_turn'
            or effect.get('card') != '$target.0' or dict(target_schema or {}) != _SCHEMA): return ()
    try: TemporaryAttackRequirementSpec.from_dict(effect.get('restriction'))
    except (TypeError, ValueError): return ()
    if not {'cr-611-continuous-effects', 'cr-508-declare-attackers-step', 'cr-115-targets'} <= set(mechanic_ids): return ()
    return _MINIMUM


def temporary_attack_requirement_runtime_dependencies(program):
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            spec = value.get('restriction')
            if (value.get('op') == 'grant_declaration_restriction_until_end_of_turn'
                    and isinstance(spec, Mapping) and spec.get('kind') == TEMPORARY_ATTACK_REQUIREMENT_KIND):
                TemporaryAttackRequirementSpec.from_dict(spec)
                if set(value) != {'op', 'card', 'restriction'} or value.get('card') != '$target.0':
                    raise ValueError('Resolved attack requirements require one canonical creature target')
                required.update(_MINIMUM)
            for child in value.values(): visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value: visit(child)
    visit((program.effects, program.handlers))
    if required and dict(program.target_schema or {}) != _SCHEMA:
        raise ValueError('Resolved attack requirements need one unrestricted creature target declaration')
    return tuple(sorted(required))
