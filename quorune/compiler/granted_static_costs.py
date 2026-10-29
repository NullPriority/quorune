from __future__ import annotations

"""Compile a cost-only static child through the existing typed grant shell."""

from dataclasses import replace

from ..ability_fragments import StaticComponentSpec, ability_fragment_to_dict
from ..static_component_scope import GRANTED_STATIC_COST_CAPABILITY, GRANTED_STATIC_COST_EVENT
from ..targeted_spell_costs import TARGETED_SPELL_COST_HANDLER, TargetedSpellCostSpec, is_granted_source_tax


def compile_static_cost_or_ordinary_inner(ordinary, **values):
    from .targeted_spell_costs import targeted_spell_cost_template
    from .static_runtime_nodes import runtime_handler_node
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    compiled = targeted_spell_cost_template(values['material_line'], source_name=values['card_name'])
    if compiled is None or not is_granted_source_tax(TargetedSpellCostSpec.from_dict(compiled[1]['modifier'])):
        from .granted_characteristic_nodes import compile_granted_characteristic_inner
        characteristic = compile_granted_characteristic_inner(**values)
        if characteristic is not None:
            return characteristic
        return ordinary(**values)
    return runtime_handler_node(node_id=values['node_id'], line=values['line'], span=values['span'],
        compiled=compiled, kind='static_ability', event=GRANTED_STATIC_COST_EVENT, active_zone='battlefield',
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Granted source tax requires its ordinary fixed spell-cost owner',
        capability_registry=values['capability_registry'], capability_profile=values['capability_profile'],
        residuals=values['residuals'])


def static_cost_granted_plan(*, node, oracle_id, face_id, source_line, node_id):
    if (node.event != GRANTED_STATIC_COST_EVENT or node.effects or len(node.handlers) != 1
            or node.handlers[0].get('handler_id') != TARGETED_SPELL_COST_HANDLER):
        return None
    try:
        from ..semantic_runtime.targeted_spell_costs import TargetedSpellCostHandler
        spec = TargetedSpellCostHandler().validate(node.handlers[0])
    except (TypeError, ValueError, KeyError):
        return None
    if not is_granted_source_tax(spec):
        return None
    from .attached_granted_ability_nodes import (AttachedGrantedAbilityPlan,
        GRANTED_STATIC_ABILITY_KIND, attached_granted_program_ability_id)
    ability_id = attached_granted_program_ability_id(kind=GRANTED_STATIC_ABILITY_KIND,
        face_id=face_id, line=source_line, node_id=node_id)
    key = f'{oracle_id}:{ability_id}'
    return AttachedGrantedAbilityPlan(GRANTED_STATIC_ABILITY_KIND, ability_id, key,
        ability_fragment_to_dict(StaticComponentSpec(key, 2, GRANTED_STATIC_COST_EVENT)),
        capability_dependencies=(GRANTED_STATIC_COST_CAPABILITY,))


def bind_granted_plan_dependencies(node, plan, registry, profile):
    if not plan.capability_dependencies:
        return node
    if registry is None:
        return None
    dependencies = tuple(sorted({*node.capability_dependencies, *plan.capability_dependencies}))
    closure = registry.closure(dependencies, profile=profile)
    if not closure.trusted:
        return None
    return replace(node, capability_dependencies=dependencies, capability_closure=closure.reachable,
        capability_profile=closure.profile, capability_fingerprint=closure.fingerprint)
