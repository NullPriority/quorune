from __future__ import annotations

"""Descriptor-owned capability minima for printed and granted keywords."""

from ..ability_fragments import (ability_fragment_from_dict,ProtectionSpec,ToxicSpec,
    CombatKeywordTriggerSpec,SpellCastKeywordTriggerSpec)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES,TRIGGERED_KEYWORD_GRANT_CAPABILITIES


def registered_component_capabilities(descriptor,registered):
    """Bind direct capabilities for the validated descriptor variant."""
    from ..semantic_runtime.ability_fragments import (
        TRIGGER_MULTIPLIER_FRAGMENT_HANDLER_ID, TriggerMultiplierAbilityFragmentHandler,
        WARD_SACRIFICE_FRAGMENT_HANDLER_ID, WardSacrificeAbilityFragmentHandler,
    )
    from ..semantic_runtime.cross_zone_type_additions import (
        CROSS_ZONE_TYPE_ADDITION_HANDLER, CrossZoneTypeAdditionHandler,
    )
    if descriptor.get("handler_id") == CROSS_ZONE_TYPE_ADDITION_HANDLER:
        return CrossZoneTypeAdditionHandler().required_capabilities(descriptor)

    from ..semantic_runtime.global_alternative_costs import GLOBAL_ALTERNATIVE_HANDLER, GlobalAlternativeCostHandler
    if descriptor.get("handler_id") == GLOBAL_ALTERNATIVE_HANDLER:
        return GlobalAlternativeCostHandler().required_capabilities(descriptor)

    from ..semantic_runtime.subtype_composite_grants import (
        SUBTYPE_COMPOSITE_GRANT_HANDLER, FixedSubtypeCompositeGrantHandler,
    )
    if descriptor.get("handler_id") == SUBTYPE_COMPOSITE_GRANT_HANDLER:
        return FixedSubtypeCompositeGrantHandler().required_capabilities(descriptor)

    dependencies=tuple(registered['capability_dependencies'])
    from ..kicker import KICKER_COST_HANDLER_ID, POSITIVE_GENERIC_KICKER_CAPABILITY
    from ..semantic_runtime.kicker import FixedManaKickerHandler

    if descriptor.get("handler_id") == KICKER_COST_HANDLER_ID:
        spec = FixedManaKickerHandler().validate(descriptor)
        return (POSITIVE_GENERIC_KICKER_CAPABILITY,) if spec.has_positive_generic_x else dependencies

    if descriptor.get("handler_id") == "rule.cast.prohibition.fixed-public.v1":
        from ..semantic_runtime.static_cast_rules import static_cast_descriptor_variant_dependencies

        if timing := static_cast_descriptor_variant_dependencies(descriptor):
            return timing
    from ..semantic_runtime.fixed_characteristic_settings import (
        NONOWNER_CREATURE_MODIFIER_HANDLER, NonownerCreatureModifierHandler,
    )
    if descriptor.get("handler_id") == NONOWNER_CREATURE_MODIFIER_HANDLER:
        return NonownerCreatureModifierHandler().required_capabilities(descriptor)
    from .live_modifier_quantities import is_controller_turn_life_loss_modifier
    if is_controller_turn_life_loss_modifier(descriptor):
        from ..characteristic_fragments import CONTROLLER_TURN_LIFE_LOSS_CAPABILITIES

        return (*dependencies, *CONTROLLER_TURN_LIFE_LOSS_CAPABILITIES)
    if descriptor.get("handler_id") == WARD_SACRIFICE_FRAGMENT_HANDLER_ID:
        fragment = WardSacrificeAbilityFragmentHandler().validate(descriptor)
        if fragment.capability_id not in dependencies:
            raise ValueError("Ward sacrifice variant has no registered owner")
        return (fragment.capability_id,)
    if descriptor.get('handler_id')!=TRIGGER_MULTIPLIER_FRAGMENT_HANDLER_ID:return dependencies
    fragment=TriggerMultiplierAbilityFragmentHandler().validate(descriptor)
    if fragment.capability_id not in dependencies:
        raise ValueError('Trigger multiplier variant has no registered owner')
    return (fragment.capability_id,)


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
        if value.get('kind') == 'enchant_typed':
            from .enchant_public_state import public_state_enchant_descriptor_dependencies
            own.update(public_state_enchant_descriptor_dependencies(value))
        if value.get('kind') == 'public_fact_at_least' and value.get('fact') == 'source_renowned':
            from ..continuous_conditions import FixedPublicStateConditionSpec
            FixedPublicStateConditionSpec.from_dict(value)
            own.add('permanent.designation.renowned')
        from ..semantic_runtime.counter_placement_keyword_grants import (
            COUNTER_PLACEMENT_KEYWORD_GRANT_HANDLER, CounterPlacementKeywordGrantHandler,
        )
        if value.get("handler_id") == COUNTER_PLACEMENT_KEYWORD_GRANT_HANDLER:
            node = CounterPlacementKeywordGrantHandler().validate(value)
            for keyword in node.abilities:
                own.update(FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES.get(keyword, ()))
        if value.get('handler_id')=='continuous.ability.disjoint-negative-keyword-grant.v1':
            from ..semantic_runtime.negative_keyword_grants import DisjointNegativeKeywordGrantHandler,NEGATIVE_KEYWORD_GRANT_CAPABILITY
            DisjointNegativeKeywordGrantHandler().validate(value)
            own.add(NEGATIVE_KEYWORD_GRANT_CAPABILITY)
        if value.get('handler_id') == 'continuous.basic_land_type.fixed-addition.v1':
            from ..basic_type_addition_model import BasicTypeAdditionSpec
            own.add(BasicTypeAdditionSpec.from_dict(value['addition']).capability_id)
        if value.get('kind') in {'declaration_cost','declaration_requirement','declaration_restriction'}:
            from ..declaration_fragments import DECLARATION_COMPONENT_CAPABILITY_ID
            # Decode the actual persisted fragment; annotations cannot replace
            # the capability required by a nested executable declaration rule.
            fragment = ability_fragment_from_dict(value)
            own.add(DECLARATION_COMPONENT_CAPABILITY_ID)
            owner = getattr(fragment, 'capability_id', None)
            if owner is not None: own.add(owner)
            from ..battlefield_mana_value_query import even_mana_value_blocker_dependencies

            own.update(even_mana_value_blocker_dependencies(fragment))
            if value.get('kind') == 'declaration_requirement' and fragment.condition is not None:
                own.add('continuous.characteristics.fixed_public_state')
        if value.get('kind') in {'static_component','copiable_program_source','copiable_static_instance','trigger_multiplier'}:
            fragment = ability_fragment_from_dict(value)
            owner = getattr(fragment, 'capability_id', None)
            if owner is not None: own.add(owner)
        if value.get('kind')=='bound_static_declaration_rules':
            own.update(ability_fragment_from_dict(value).required_capabilities())
        if value.get('kind') in {'granted_activated','attached_untap_restraint','enchanted_player_attack_requirement','activation_prohibition','mutate'}:
            fragment = ability_fragment_from_dict(value)
            owner = getattr(fragment, 'capability_id', None)
            if owner is not None: own.add(owner)
        fragment_data = value.get("value")
        if (value.get("kind") == "ward" and isinstance(fragment_data, Mapping)
                and fragment_data.get("life_payment") is not None):
            own.add(ability_fragment_from_dict(value).capability_id)
        if (value.get("kind") == "block_capacity" and isinstance(fragment_data, Mapping)
                and fragment_data.get("schema_version") == 2):
            from ..block_capacity_model import EQUIPMENT_BLOCK_CAPACITY_CAPABILITY

            ability_fragment_from_dict(value)
            own.add(EQUIPMENT_BLOCK_CAPACITY_CAPABILITY)
        if value.get('kind') == 'granted_triggered' and value.get('value', {}).get('schema_version') == 2:
            from ..cipher_model import CIPHER_CAPABILITY
            ability_fragment_from_dict(value)
            own.add(CIPHER_CAPABILITY)
        return {*own, *(cap for child in value.values() for cap in _fragment_descriptor_requirements(child))}
    if isinstance(value, (list, tuple)):
        return {cap for child in value for cap in _fragment_descriptor_requirements(child)}
    return set()


def _granted_evolve_dependencies(program):
    from collections.abc import Mapping
    from ..evolve import EVOLVE_EVENT_CONDITION_FIELD

    condition = program.event_condition
    if not program.provenance.get("granted_only"):
        return ()
    evolves = (isinstance(condition, Mapping) and condition.get("field") == EVOLVE_EVENT_CONDITION_FIELD
               or "evolve" in program.coverage)
    if not evolves:
        return ()
    expected_effect = {
        "op": "place_counters", "card": "$source", "counter": "+1/+1", "amount": 1, "source": "$source",
    }
    if ((program.event, program.active_zone) != ("creature.enter", "battlefield")
            or condition != {"field": EVOLVE_EVENT_CONDITION_FIELD, "op": "truthy"}
            or "intervening_condition" not in program.coverage
            or program.cost_schema is not None or program.target_schema is not None or program.handlers
            or tuple(program.effects) != (expected_effect,)):
        raise ValueError("A granted Evolve program requires its exact entry comparison and source counter")
    return ("counter.producer.evolve", "counter.producer.fixed_effect")


def keyword_runtime_dependencies(program):
    from ..cipher_model import CIPHER_CAPABILITY
    required = set(attached_keyword_runtime_dependencies(program))
    from .commander_companion_roles import companion_role_runtime_dependencies

    required.update(companion_role_runtime_dependencies(program))
    from ..read_ahead import read_ahead_grant_descriptor_dependencies

    for descriptor in program.handlers:
        granted_read_ahead = read_ahead_grant_descriptor_dependencies(descriptor)
        if granted_read_ahead and (program.event, program.active_zone) != ("characteristics.evaluate", "battlefield"):
            raise ValueError("Read Ahead grants require a current battlefield static component")
        required.update(granted_read_ahead)
    required.update(_granted_evolve_dependencies(program))
    from .recover import recover_program_dependencies
    required.update(recover_program_dependencies(program))
    from ..declared_cast_cost_model import declared_cost_event_dependencies
    required.update(declared_cost_event_dependencies(getattr(program, 'condition', None)))
    if any(effect.get('op') == 'encode_cipher' for effect in program.effects):
        required.add(CIPHER_CAPABILITY)
    if 'fixed_keyword_event_grant' in program.coverage:
        required.add('continuous.ability.fixed_keyword_event_grant')
    if 'banding' in program.coverage:
        required.add('combat.banding')
    required.update(_fragment_descriptor_requirements((program.handlers, program.effects)))
    from .mana_point_result import mana_point_runtime_dependencies
    required.update(mana_point_runtime_dependencies(program))
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
    if (program.provenance.get('granted_only') and program.active_zone != 'command' and program.event == 'characteristics.evaluate'
            and program.ability_id.startswith('static:granted:')):
        from ..rules.granted_characteristic_shapes import granted_characteristic_handlers_are_closed
        from ..static_component_scope import GRANTED_STATIC_CHARACTERISTIC_CAPABILITY
        if not granted_characteristic_handlers_are_closed(program.handlers):
            raise ValueError('Granted static characteristic child has an unrepresented shape')
        required.add(GRANTED_STATIC_CHARACTERISTIC_CAPABILITY)
    return tuple(sorted(required))
