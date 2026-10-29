from __future__ import annotations

"""Descriptor-owned capability minima for printed and granted keywords."""

from ..ability_fragments import (ability_fragment_from_dict,ProtectionSpec,ToxicSpec,
    CombatKeywordTriggerSpec,SpellCastKeywordTriggerSpec)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES,TRIGGERED_KEYWORD_GRANT_CAPABILITIES


def attached_keyword_runtime_dependencies(program):
    from ..semantic_runtime.attached_continuous import AttachedFixedCharacteristicsHandler
    handler=AttachedFixedCharacteristicsHandler();required=set()
    for descriptor in program.handlers:
        if descriptor.get('handler_id')!=handler.handler_id:continue
        node=handler.validate(descriptor)
        for keyword in (*node.add_abilities,*node.remove_abilities):
            required.update(FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES.get(keyword,()))
        for value in node.add_ability_fragments:
            fragment=ability_fragment_from_dict(value)
            owner=getattr(fragment,'capability_id',None)
            if isinstance(owner,str):required.add(owner)
            if isinstance(fragment,ProtectionSpec):required.add('protection.typed.debt')
            elif isinstance(fragment,ToxicSpec):required.add('damage.result.toxic')
            elif isinstance(fragment,(CombatKeywordTriggerSpec,SpellCastKeywordTriggerSpec)):
                label=fragment.kind.value.replace('_',' ').title()
                owner=TRIGGERED_KEYWORD_GRANT_CAPABILITIES.get(label)
                if owner is not None:required.add(owner)
                elif label=='Bushido':required.add('combat.trigger.bushido')
    return tuple(sorted(required))


def _fragment_descriptor_requirements(value):
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        keyword_arrays = (value.get(field, ()) for field in ('keywords', 'add_abilities', 'remove_abilities'))
        own = {'combat.banding'} if any(isinstance(words, (list, tuple)) and 'Banding' in words
            for words in keyword_arrays) else set()
        if value.get('kind') == 'public_fact_at_least' and value.get('fact') == 'source_renowned':
            from ..continuous_conditions import FixedPublicStateConditionSpec
            FixedPublicStateConditionSpec.from_dict(value)
            own.add('permanent.designation.renowned')
        if value.get('kind') in {'declaration_cost','declaration_requirement','declaration_restriction'}:
            from ..declaration_fragments import DECLARATION_COMPONENT_CAPABILITY_ID
            # Decode the actual persisted fragment; annotations cannot replace
            # the capability required by a nested executable declaration rule.
            fragment = ability_fragment_from_dict(value)
            own.add(DECLARATION_COMPONENT_CAPABILITY_ID)
            owner = getattr(fragment, 'capability_id', None)
            if owner is not None: own.add(owner)
            if value.get('kind') == 'declaration_requirement' and fragment.condition is not None:
                own.add('continuous.characteristics.fixed_public_state')
        if value.get('kind') == 'static_component':
            fragment = ability_fragment_from_dict(value)
            owner = getattr(fragment, 'capability_id', None)
            if owner is not None: own.add(owner)
        if value.get('kind') == 'granted_activated':
            fragment = ability_fragment_from_dict(value)
            owner = getattr(fragment, 'capability_id', None)
            if owner is not None: own.add(owner)
        return {*own, *(cap for child in value.values() for cap in _fragment_descriptor_requirements(child))}
    if isinstance(value, (list, tuple)):
        return {cap for child in value for cap in _fragment_descriptor_requirements(child)}
    return set()


def keyword_runtime_dependencies(program):
    required = set(attached_keyword_runtime_dependencies(program))
    if 'fixed_keyword_event_grant' in program.coverage:
        required.add('continuous.ability.fixed_keyword_event_grant')
    if 'banding' in program.coverage:
        required.add('combat.banding')
    required.update(_fragment_descriptor_requirements((program.handlers, program.effects)))
    if program.provenance.get('granted_only') and program.ability_id.startswith('ability:granted:'):
        from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors
        from ..granted_activation_model import RESTRICTED_MANA_GRANT_CAPABILITY
        from ..fixed_mana_abilities import FIXED_MANA_HANDLER_ID, FixedActivatedManaAbilitySpec
        restrictions = [ability.mana_spend_restriction
            for ability in activated_abilities_from_descriptors(program.handlers)]
        restrictions.extend(FixedActivatedManaAbilitySpec.from_dict(descriptor['ability']).spend_restriction
            for descriptor in program.handlers if descriptor.get('handler_id') == FIXED_MANA_HANDLER_ID)
        if any(restriction not in {None, 'nonartifact_spell_prohibited'} for restriction in restrictions):
            required.add(RESTRICTED_MANA_GRANT_CAPABILITY)
    if program.provenance.get('granted_only') and any(
            value.get('handler_id') == 'modification.cast-cost.targeted-fixed.v1' for value in program.handlers):
        from ..static_component_scope import GRANTED_STATIC_COST_CAPABILITY
        required.add(GRANTED_STATIC_COST_CAPABILITY)
    if (program.provenance.get('granted_only') and program.event == 'characteristics.evaluate'
            and program.ability_id.startswith('static:granted:')):
        from ..rules.granted_characteristic_shapes import granted_characteristic_handlers_are_closed
        from ..static_component_scope import GRANTED_STATIC_CHARACTERISTIC_CAPABILITY
        if not granted_characteristic_handlers_are_closed(program.handlers):
            raise ValueError('Granted static characteristic child has an unrepresented shape')
        required.add(GRANTED_STATIC_CHARACTERISTIC_CAPABILITY)
    return tuple(sorted(required))
