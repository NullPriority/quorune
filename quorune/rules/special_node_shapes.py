from __future__ import annotations
from collections.abc import Mapping as MappingABC


def special_trigger_envelope_capabilities(dependencies, mechanics):
    """Preserve ordinary event authority after a nonempty special-body proof."""
    if not dependencies:return dependencies
    from .capabilities import MECHANIC_CAPABILITY_DEPENDENCIES,_SHAPE_GATED_MECHANICS
    events={m for m in mechanics if m not in _SHAPE_GATED_MECHANICS
        and (m=='fixed-typed-event-effect-trigger' or m.startswith('trigger-event-'))}
    if 'third-current-player-life-loss' in mechanics and 'cr-603-handling-triggered-abilities' in mechanics:
        events.add('fixed-typed-event-effect-trigger')
    return tuple(sorted({*dependencies,
        *(cap for m in events for cap in MECHANIC_CAPABILITY_DEPENDENCIES.get(m,())),
        *(('trigger.placement.apnap',) if 'cr-603-handling-triggered-abilities' in mechanics else ())}))


def special_result_trigger_runtime_dependencies(program):
    """Bind executable Manifest/half-life results and their subscriptions."""
    from collections.abc import Mapping
    from .manifest_shapes import manifest_node_capabilities, validate_manifest_result_envelope
    from ..fractional_quantity_model import HalfQuantitySpec,HALF_QUANTITY_KIND,HALF_LIFE_CAPABILITY
    validate_manifest_result_envelope(program)
    from .third_life_shapes import third_life_program_dependencies
    third=third_life_program_dependencies(program)
    required=set(third);manifest=False;half=bool(third)
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
                if getattr(spec.fact,'value',None)=='unique_player_maximum':required.add('condition.player.unique_maximum')
                if getattr(spec.fact,'value',None)=='opponent_has_greater_metric':required.add('condition.player.opponent_greater_metric')
            for child in value.values():condition(child)
        elif isinstance(value,(list,tuple)):
            for child in value:condition(child)
    condition(program.event_condition)
    return tuple(sorted(required))


def _owned_special_result_capabilities(**context):
    """Keep independently owned special results ahead of generic amount walking."""
    from .optional_sacrifice_stats import optional_sacrifice_stat_node_capabilities
    if owned := optional_sacrifice_stat_node_capabilities(**context):return owned
    from .life_gain_counted_results import life_gain_counter_payment_node_capabilities
    if owned := life_gain_counter_payment_node_capabilities(**context):return owned
    from .recover import recover_node_capabilities
    from .ripple import ripple_node_capabilities
    from .demonstrate import demonstrate_node_capabilities
    from .gravestorm import gravestorm_node_capabilities
    from .current_life_effect_amounts import current_life_node_capabilities
    from .turn_life_loss_recovery import turn_life_loss_node_capabilities
    from .per_player_query_life import per_player_query_life_node_capabilities
    from .per_player_query_damage import per_player_query_damage_node_capabilities
    from .per_player_life_damage import per_player_life_damage_node_capabilities
    from .target_player_query_amounts import target_player_query_node_capabilities
    from .attachment_source_return import attachment_source_return_node_capabilities
    from .graveyard_card_results import graveyard_card_counter_life_node_capabilities
    from .graveyard_exile_branches import graveyard_exile_branch_node_capabilities
    from .graveyard_exile_tokens import graveyard_exile_token_node_capabilities
    from .graveyard_exile_type_outcomes import graveyard_exile_type_outcome_node_capabilities
    for resolver in (gravestorm_node_capabilities,demonstrate_node_capabilities,recover_node_capabilities,ripple_node_capabilities,current_life_node_capabilities,turn_life_loss_node_capabilities,per_player_query_life_node_capabilities,per_player_query_damage_node_capabilities,per_player_life_damage_node_capabilities,target_player_query_node_capabilities,
                     attachment_source_return_node_capabilities, graveyard_card_counter_life_node_capabilities,
                     graveyard_exile_branch_node_capabilities, graveyard_exile_token_node_capabilities,
                     graveyard_exile_type_outcome_node_capabilities):
        required=resolver(**context)
        if required:return required
    return ()


def _shared_and_source_entry_capabilities(*,effects,target_schema,mechanics,cost_schema=None):
    """Keep closed library and source-entry envelopes ahead of leaf composition."""
    if any(e.get("op") == "look_top_shared_type_reveal" for e in effects):
        from .shared_type_library import shared_type_library_node_capabilities
        return shared_type_library_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    query_result=_owned_special_result_capabilities(
        effects=effects,target_schema=target_schema,mechanic_ids=mechanics,cost_schema=cost_schema)
    if query_result:return query_result
    if any('self_entry_counter' in effect for effect in effects):
        from .source_self_entry_counters import source_self_entry_counter_node_capabilities
        return source_self_entry_counter_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if any('self_entry_followup' in effect for effect in effects):
        from .self_zone_move_followups import self_entry_followup_node_capabilities
        return self_entry_followup_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    return None


def special_node_capabilities(*,effects,target_schema,mechanics,cost_schema=None):
    """Route independently owned special shapes before ordinary leaf composition."""
    from ..event_card_return import DEATH_LAND_EXILE_MECHANIC, death_land_exile_node_capabilities
    if DEATH_LAND_EXILE_MECHANIC in mechanics:
        return death_land_exile_node_capabilities(effects, target_schema)
    owned=_shared_and_source_entry_capabilities(
        effects=effects,target_schema=target_schema,mechanics=mechanics,cost_schema=cost_schema)
    if owned is not None:return owned
    if any('event_optional_draw_version' in effect for effect in effects):
        from .event_optional_draw_shapes import event_optional_draw_node_capabilities
        return event_optional_draw_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if len(effects) == 1 and 'fixed_source_sacrifice_version' in effects[0]:
        from .source_sacrifice_shapes import fixed_source_sacrifice_node_capabilities
        return fixed_source_sacrifice_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if any(effect.get('op') == 'remove_chosen_counter' for effect in effects):
        from .chosen_counter_removal import chosen_counter_removal_node_capabilities
        return chosen_counter_removal_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if any('optional_target_result' in effect for effect in effects):
        from .optional_target_results import optional_target_result_node_capabilities
        return optional_target_result_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if len(effects) == 1 and effects[0].get('op') == 'choose_target_basic_land_type':
        from .target_land_type import target_land_type_node_capabilities
        return target_land_type_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if len(effects) == 1 and effects[0].get('op') == 'recycle_graveyards_into_libraries':
        from .graveyard_recycling import graveyard_recycling_node_capabilities
        return graveyard_recycling_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if any(effect.get('op') == 'inspect_target_library' for effect in effects):
        from .target_library_inspection import target_library_inspection_node_capabilities
        return target_library_inspection_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if any(effect.get('op') == 'search_named_library_or_graveyard' for effect in effects):
        from .named_searches import named_zone_search_node_capabilities
        return named_zone_search_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if 'conditional-target-power-preparation' in mechanics or any(e.get('op') == 'apply_if_public_condition' and e.get('schema_version') == 16 for e in effects):
        from .conditional_target_preparation import conditional_prepared_power_capabilities
        return conditional_prepared_power_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if 'fixed-awaken-paid-body' in mechanics or isinstance(cost_schema,MappingABC) and 'awaken' in cost_schema:
        from .awaken_shapes import awaken_node_capabilities
        awaken=awaken_node_capabilities(effects=effects,target_schema=target_schema,mechanics=mechanics,cost_schema=cost_schema)
        if awaken is not None:return awaken
    from .event_counter_threshold import EVENT_COUNTER_THRESHOLD_MECHANIC, event_counter_threshold_node_capabilities
    if EVENT_COUNTER_THRESHOLD_MECHANIC in mechanics:
        return event_counter_threshold_node_capabilities(effects, target_schema)
    from .controlled_return import CONTROLLED_RETURN_FIELD, controlled_return_node_capabilities
    from .distinct_sacrifices import DISTINCT_SACRIFICE_FIELD, distinct_sacrifice_node_capabilities
    from ..drawing.captured_hand import CAPTURED_DRAW_CAST_FIELD, captured_draw_cast_node_capabilities
    from ..bounded_hand_cast_model import BOUNDED_HAND_CAST_FIELD, bounded_hand_cast_node_capabilities
    if len(effects) == 1 and BOUNDED_HAND_CAST_FIELD in effects[0]:
        return bounded_hand_cast_node_capabilities(effects=effects, target_schema=target_schema)
    if len(effects) == 1 and CAPTURED_DRAW_CAST_FIELD in effects[0]:
        return captured_draw_cast_node_capabilities(effects=effects, target_schema=target_schema)
    if len(effects) == 1 and DISTINCT_SACRIFICE_FIELD in effects[0]:
        return distinct_sacrifice_node_capabilities(effects=effects, target_schema=target_schema)
    if len(effects) == 1 and CONTROLLED_RETURN_FIELD in effects[0]:
        return controlled_return_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
    from ..alternative_discard_model import ALTERNATIVE_DISCARD_MECHANIC
    if ALTERNATIVE_DISCARD_MECHANIC in mechanics:
        from .alternative_discard_shapes import alternative_discard_node_capabilities
        return alternative_discard_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    from .graveyard_card_targets import OPPOSING_GRAVEYARD_RETURN_MECHANIC, opposing_graveyard_return_sequence_capabilities
    if OPPOSING_GRAVEYARD_RETURN_MECHANIC in mechanics:
        return opposing_graveyard_return_sequence_capabilities(effects=effects, target_schema=target_schema)
    from ..targeted_graveyard_cast_model import GRAVEYARD_CAST_MECHANIC
    if GRAVEYARD_CAST_MECHANIC in mechanics:
        from .graveyard_card_targets import targeted_graveyard_cast_node_capabilities
        return targeted_graveyard_cast_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    from ..compiler.target_set_result_sequences import TARGET_SET_SEQUENCE_MECHANIC
    if TARGET_SET_SEQUENCE_MECHANIC in mechanics:
        from .target_set_result_sequences import target_set_result_sequence_capabilities
        return target_set_result_sequence_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    from collections.abc import Mapping
    from ..compiler.per_player_target_sets import PER_PLAYER_TARGET_MECHANIC
    if (PER_PLAYER_TARGET_MECHANIC in mechanics or isinstance(target_schema,Mapping)
            and {'distinct_controllers','retain_controller'}.intersection(target_schema)):
        from .per_player_target_sets import per_player_target_node_capabilities
        return per_player_target_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    from ..phasing_model import PHASE_OUT_OPERATION
    from .occurrence_object_results import phase_out_node_capabilities
    if any(effect.get("op") == PHASE_OUT_OPERATION for effect in effects):
        return phase_out_node_capabilities(effects, target_schema)
    from ..face_up_effect_model import FACE_UP_EFFECT_MECHANIC,FACE_UP_EFFECT_OPERATION,face_up_node_capabilities
    if FACE_UP_EFFECT_MECHANIC in mechanics or any(effect.get('op')==FACE_UP_EFFECT_OPERATION for effect in effects):
        return face_up_node_capabilities(effects=effects,target_schema=target_schema,mechanics=mechanics)
    if 'all-kind-counter-doubling' in mechanics and 'any-number-target-set' not in mechanics:
        from .all_kind_counter_shapes import all_kind_counter_node_capabilities
        return all_kind_counter_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if 'any-number-target-set' in mechanics:
        from .any_number_target_shapes import any_number_target_node_capabilities
        return any_number_target_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if 'quantified-counter-target-set' in mechanics:
        from .counter_target_set_shapes import quantified_counter_target_capabilities
        return quantified_counter_target_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    from .spell_self_exile import TERMINAL_SPELL_MOVE_MECHANICS, terminal_spell_move_node_capabilities
    if TERMINAL_SPELL_MOVE_MECHANICS.intersection(mechanics):
        return terminal_spell_move_node_capabilities(effects=effects,target_schema=target_schema,
            mechanics=mechanics,cost_schema=cost_schema)
    from ..compiler.reanimation_templates import RELATIVE_TARGET_REANIMATION_MECHANIC
    if RELATIVE_TARGET_REANIMATION_MECHANIC in mechanics:
        from ..reanimation_entry_counters import REANIMATION_ENTRY_COUNTER_MECHANIC
        if REANIMATION_ENTRY_COUNTER_MECHANIC in mechanics:
            from .reanimation_entry_counters import reanimation_entry_counter_node_capabilities
            return reanimation_entry_counter_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
        from .reanimation_capability_shapes import relative_target_reanimation_node_capabilities
        return relative_target_reanimation_node_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics)
    from .graveyard_copy_cast_model import GRAVEYARD_COPY_MECHANIC
    if GRAVEYARD_COPY_MECHANIC in mechanics:
        from .graveyard_card_targets import graveyard_copy_cast_node_capabilities
        return graveyard_copy_cast_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
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
    if 'sequential-public-conditions' not in mechanics and (RESOLUTION_CONDITION_MECHANIC in mechanics or RESOLUTION_CONDITION_OPERATION in nested_effect_operations(effects)):
        from .resolution_condition_shapes import resolution_condition_node_capabilities
        return resolution_condition_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics,cost_schema=cost_schema)
    from .closed_effect_program_shapes import CLOSED_EFFECT_PROGRAM_MECHANIC
    if not mechanics.intersection({
        'fixed-nonrepeating-modal', 'fixed-choose-one-modal-spell', CLOSED_EFFECT_PROGRAM_MECHANIC,
    }):
        from .fractional_player_result_shapes import half_life_node_capabilities, half_choice_node_capabilities
        if 'third-current-player-life-loss' in mechanics:
            from .third_life_shapes import third_life_node_capabilities
            return third_life_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
        if 'half-current-player-life-loss' in mechanics:
            return half_life_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
        if 'half-affected-player-domain-choice' in mechanics:
            return half_choice_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    return None


def special_node_covered_mechanics(supplied):
    """Keep special capability coverage with its typed shape routing."""
    from ..phasing_model import PHASE_OUT_CAPABILITY, PHASE_OUT_MECHANIC

    mapping = {
        PHASE_OUT_CAPABILITY: PHASE_OUT_MECHANIC,
        "continuous.resolution.target_basic_land_type_choice": "target-basic-land-type-choice",
        "resolution.effect_program.optional_target_result": "fixed-optional-target-characteristic-result",
        "counter.removal.chosen_kind": "chosen-counter-removal",
        "resolution.event_player.optional_draw": "fixed-event-player-optional-draw",
        'library.top.shared_type_optional_reveal': 'private-top-shared-type-reveal',
        'choice.affected_player.graveyard_exile': 'target-player-graveyard-exile-choice',
        'quantity_expression.resolution_paid_creature_stat': 'optional-sacrifice-creature-stat-result',
        'life.loss.third_current_player': 'third-current-player-life-loss',
        'keyword.recover.fixed_mana': 'recover-fixed-mana',
        'keyword.recover.half_current_life': 'recover-half-current-life',
        'quantity_expression.current_controller_life': 'current-controller-life-effect-amount',
        'quantity_expression.all_player_turn_life_loss': 'all-player-turn-life-loss-amount',
        'life.loss.per_player_public_quantity': 'per-player-public-query-life-loss',
        'damage.batch.per_player_public_quantity': 'per-player-public-query-damage',
        'damage.batch.per_player_current_life_fraction': 'per-player-current-life-fraction-damage',
        'quantity_expression.target_player_public_query': 'target-player-public-query-amount',
        'casting.resolution.targeted_graveyard': 'targeted-resolving-graveyard-cast',
        'casting.resolution.graveyard_copies': 'resolving-graveyard-copy-cast',
        'casting.resolution.fixed_bounded_hand': 'fixed-bounded-hand-free-cast',
    }
    return {mechanic for capability, mechanic in mapping.items() if capability in supplied}
