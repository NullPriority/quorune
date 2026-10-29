from __future__ import annotations


def special_trigger_envelope_capabilities(dependencies, mechanics):
    """Preserve ordinary event authority after a nonempty special-body proof."""
    if not dependencies:return dependencies
    from .capabilities import MECHANIC_CAPABILITY_DEPENDENCIES,_SHAPE_GATED_MECHANICS
    events={m for m in mechanics if m not in _SHAPE_GATED_MECHANICS
        and (m=='fixed-typed-event-effect-trigger' or m.startswith('trigger-event-'))}
    return tuple(sorted({*dependencies,
        *(cap for m in events for cap in MECHANIC_CAPABILITY_DEPENDENCIES.get(m,())),
        *(('trigger.placement.apnap',) if 'cr-603-handling-triggered-abilities' in mechanics else ())}))


def special_result_trigger_runtime_dependencies(program):
    """Bind executable Manifest/half-life results and their subscriptions."""
    from collections.abc import Mapping
    from .manifest_shapes import manifest_node_capabilities
    from ..fractional_quantity_model import HalfQuantitySpec,HALF_QUANTITY_KIND,HALF_LIFE_CAPABILITY
    required=set();manifest=False;half=False
    for effect in program.effects:
        if effect.get('op')=='manifest_card':
            owned=manifest_node_capabilities(effects=(effect,),target_schema=None,mechanics={'manifest'})
            if not owned:raise ValueError('Manifest result has a closed current-controller shape')
            required.update(owned);manifest=True
        amount=effect.get('amount')
        if effect.get('op')=='lose_life' and isinstance(amount,Mapping) and amount.get('kind')==HALF_QUANTITY_KIND:
            HalfQuantitySpec.from_dict(amount);required.add(HALF_LIFE_CAPABILITY);half=True
    if not required:return ()
    if program.event in {'resolve','activate','characteristics.evaluate','intrinsic_ability'}:return tuple(sorted(required))
    required.update({'trigger.effect.fixed_event','trigger.placement.apnap'})
    event=program.event
    if manifest and not half and event in {'permanent.enter.self','creature.dies.self','permanent.graveyard.self'}:
        # Direct source entry/death leaves predate the generic event-result
        # wrapper and retain their separately owned normalized subscription.
        required.discard('trigger.effect.fixed_event')
    if event=='creature.attacks':
        source={'field':'card','op':'eq','value':'$source.ref'}
        required.add('trigger.event.normalized_self_attack' if program.event_condition==source else 'trigger.event.normalized_public_action')
    elif event.startswith('damage.'):
        required.add('trigger.event.normalized_damage')
    elif event=='spell.cast':
        required.add('trigger.event.normalized_spell_cast')
    elif event.removesuffix('.self') in {'land.enter','permanent.enter','artifact.enter','creature.enter','enchantment.enter','creature.dies','permanent.graveyard','artifact.graveyard','permanent.leave'}:
        required.add('trigger.event.normalized_zone_change')
    def condition(value):
        if isinstance(value,Mapping):
            if value.get('field')=='fixed_public_state_condition':
                from ..continuous_conditions import FixedPublicStateConditionSpec,FixedPublicStateConditionKind
                spec=FixedPublicStateConditionSpec.from_dict(value.get('condition'))
                required.add('trigger.condition.fixed_public_state')
                if spec.kind is FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS:required.add('condition.creature.fixed_numeric_exists')
            for child in value.values():condition(child)
        elif isinstance(value,(list,tuple)):
            for child in value:condition(child)
    condition(program.event_condition)
    return tuple(sorted(required))


def special_node_capabilities(*,effects,target_schema,mechanics,cost_schema=None):
    """Route independently owned special shapes before ordinary leaf composition."""
    if 'all-kind-counter-doubling' in mechanics and 'any-number-target-set' not in mechanics:
        from .all_kind_counter_shapes import all_kind_counter_node_capabilities
        return all_kind_counter_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if 'any-number-target-set' in mechanics:
        from .any_number_target_shapes import any_number_target_node_capabilities
        return any_number_target_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if 'quantified-counter-target-set' in mechanics:
        from .counter_target_set_shapes import quantified_counter_target_capabilities
        return quantified_counter_target_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if 'spell-resolution-self-exile' in mechanics:
        from .spell_self_exile import spell_self_exile_node_capabilities
        return spell_self_exile_node_capabilities(effects=effects,target_schema=target_schema,
            mechanics=mechanics,cost_schema=cost_schema)
    if 'manifest' in mechanics and all(effect.get('op') == 'manifest_card' for effect in effects):
        from .manifest_shapes import manifest_node_capabilities
        return manifest_node_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics)
    if 'predefined-role-token' in mechanics:
        from .role_token_shapes import role_token_node_capabilities
        return role_token_node_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics)
    if 'transmute' in mechanics:
        from ..transmute import transmute_node_capabilities
        return transmute_node_capabilities(effects=effects, target_schema=target_schema,
            mechanic_ids=mechanics, cost_schema=cost_schema)
    from .temporary_ability_grant import TEMPORARY_QUOTED_GRANT_MECHANIC, temporary_quoted_grant_node_capabilities
    if TEMPORARY_QUOTED_GRANT_MECHANIC in mechanics:
        return temporary_quoted_grant_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics, cost_schema=cost_schema)
    if 'backup' in mechanics:
        from .temporary_ability_grant import backup_node_capabilities
        return backup_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics,cost_schema=cost_schema)
    if 'extort' in mechanics:
        from .extort import extort_node_capabilities
        return extort_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics,cost_schema=cost_schema)
    if 'public-quantity-mana' in mechanics:
        from .public_quantity_mana_shapes import public_quantity_mana_node_capabilities
        return public_quantity_mana_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC,RESOLUTION_CONDITION_OPERATION
    from .effect_shape_walkers import nested_effect_operations
    if RESOLUTION_CONDITION_MECHANIC in mechanics or RESOLUTION_CONDITION_OPERATION in nested_effect_operations(effects):
        from .resolution_condition_shapes import resolution_condition_node_capabilities
        return resolution_condition_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics,cost_schema=cost_schema)
    if not mechanics.intersection({'fixed-nonrepeating-modal', 'fixed-choose-one-modal-spell'}):
        from .fractional_player_result_shapes import half_life_node_capabilities, half_choice_node_capabilities
        if 'half-current-player-life-loss' in mechanics:
            return half_life_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
        if 'half-affected-player-domain-choice' in mechanics:
            return half_choice_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    return None
