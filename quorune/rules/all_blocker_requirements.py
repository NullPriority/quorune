from __future__ import annotations

"""Current giver-owned blocking requirements, projected at declaration time."""

from ..ability_fragments import declaration_requirement_specs
from ..card_programs.runtime import fixed_public_state_condition_matches
from ..dynamic_characteristics import query_characteristic_count
from ..continuous_effect_state import active_resolution_declaration_rule_effects
from ..declaration_fragments import DeclarationRequirementTemplate


def _condition_matches(host, source, requirement):
    if requirement.condition is None:
        return True
    return fixed_public_state_condition_matches(host.state, source, requirement.condition,
        public_object_resolver=host._public_object_query_result,
        quantity_resolver=lambda card,spec:query_characteristic_count(host,card,spec),require_available=True)


def current_attacker_block_requirements(host, attacker, *, error_type):
    result = []
    for index, requirement in enumerate(declaration_requirement_specs(
            host._effective_ability_fragments(attacker,error_type=error_type))):
        if requirement.scope == 'self' and _condition_matches(host,attacker,requirement):
            result.append((f'self:{index}',requirement))
    for source in sorted(host.state.cards.values(),key=lambda card:card.ref):
        if (source.zone != 'battlefield' or source.phased_out or source.attached_to != attacker.object_id):
            continue
        for index,requirement in enumerate(declaration_requirement_specs(
                host._effective_ability_fragments(source,error_type=error_type))):
            if requirement.scope == 'attached' and _condition_matches(host,source,requirement):
                result.append((f'attached:{source.logical_object_id}:{index}',requirement))
    for effect in sorted(active_resolution_declaration_rule_effects(host.state,attacker),
            key=lambda rule:(rule.timestamp,rule.effect_id)):
        if isinstance(effect.restriction,DeclarationRequirementTemplate):
            result.append((f'resolved:{effect.effect_id}',effect.restriction))
    return tuple(result)
