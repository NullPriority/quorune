from __future__ import annotations

from typing import Any, Mapping
from ..suspect_model import SUSPECT_OPERATION
from ..fixed_top_exile import CHOSEN_IMPULSE_GRANT_FIELD
from ..resolution_characteristic_model import (
    CHARACTERISTIC_OPERATION, DERIVED_CHARACTERISTIC_CAPABILITY, DERIVED_CHARACTERISTIC_INSTRUCTION_VERSION,
    INDEFINITE_SOURCE_CHARACTERISTIC_VERSIONS, fixed_resolution_characteristic_instruction,
)

def _owned_operation_payload_dependencies(value: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the closed payloads of independently owned instructions."""
    from ..rules.cast_cascade_grants import CAST_CASCADE_OPERATION, cast_cascade_payload_dependencies
    from ..rules.next_cast_counter_protection import (
        is_versioned_next_cast_protection, next_cast_protection_payload_dependencies,
    )
    from ..rules.next_spell_payment import NEXT_SPELL_PAYMENT_OPERATION, next_spell_payment_payload_dependencies
    from ..rules.impulse_access_capability_shapes import declared_impulse_access_dependencies, next_end_step_impulse_dependencies
    from ..event_spell_copy_model import NEXT_CAST_COPY_FIELD, NEXT_CAST_COPY_CAPABILITY, next_cast_copy_payload
    from ..rules.temporary_cast_timing import TEMPORARY_CAST_TIMING_FIELD, temporary_cast_timing_payload_dependencies
    if impulse := declared_impulse_access_dependencies(value) or next_end_step_impulse_dependencies(value):
        return impulse
    from ..rules.distinct_sacrifices import distinct_sacrifice_payload_dependencies
    from ..drawing.captured_hand import captured_draw_cast_payload_dependencies
    from ..dungeon_progress import venture_payload_dependencies
    from ..initiative_model import initiative_payload_dependencies
    from ..rules.manifest_shapes import manifest_result_payload_dependencies
    from ..rules.event_counter_threshold import event_counter_threshold_payload_dependencies
    if owned_choice := (distinct_sacrifice_payload_dependencies(value) or captured_draw_cast_payload_dependencies(value)
                        or venture_payload_dependencies(value) or initiative_payload_dependencies(value)
                        or manifest_result_payload_dependencies(value) or event_counter_threshold_payload_dependencies(value)):
        return owned_choice
    if CHOSEN_IMPULSE_GRANT_FIELD in value:
        raise ValueError("Chosen impulse grants are produced only by an authoritative exile choice")
    if TEMPORARY_CAST_TIMING_FIELD in value:
        return temporary_cast_timing_payload_dependencies(value)
    if NEXT_CAST_COPY_FIELD in value:
        next_cast_copy_payload(value)
        return (NEXT_CAST_COPY_CAPABILITY,)
    if is_versioned_next_cast_protection(value):
        return next_cast_protection_payload_dependencies(value)
    if value.get("op") == CAST_CASCADE_OPERATION:
        return cast_cascade_payload_dependencies(value)
    if value.get("op") == NEXT_SPELL_PAYMENT_OPERATION:
        return next_spell_payment_payload_dependencies(value)
    version = value.get("schema_version")
    if value.get("op") == "return_graveyard_card_to_owner_hand":
        from ..rules.graveyard_card_targets import public_graveyard_return_payload_dependencies

        return public_graveyard_return_payload_dependencies(value)
    if (value.get("op") == CHARACTERISTIC_OPERATION and type(version) is int
            and version in INDEFINITE_SOURCE_CHARACTERISTIC_VERSIONS):
        spec, _selection = fixed_resolution_characteristic_instruction(value)
        return spec.capability_dependencies
    if value.get("op") == CHARACTERISTIC_OPERATION and value.get("schema_version") == DERIVED_CHARACTERISTIC_INSTRUCTION_VERSION:
        fixed_resolution_characteristic_instruction(value)
        return (DERIVED_CHARACTERISTIC_CAPABILITY,)
    if value.get("op") == "detain_permanents":
        from ..rules.detain import DETAIN_CAPABILITY, validate_detain_instruction
        validate_detain_instruction(value)
        return (DETAIN_CAPABILITY,)
    if value.get("op") == "restrict_permanent_actions_until_next_turn":
        from ..rules.detain import DIRECT_PROHIBITION_CAPABILITY, validate_direct_action_prohibition
        validate_direct_action_prohibition(value)
        return (DIRECT_PROHIBITION_CAPABILITY,)
    if value.get("op") == "schedule_turn_skip":
        from ..turn_skip_model import TURN_SKIP_CAPABILITY, decode_turn_skip_instruction
        decode_turn_skip_instruction(value)
        return (TURN_SKIP_CAPABILITY,)
    if value.get("op") in ("earthbend_land", "finish_earthbend_land", "return_earthbent_objects"):
        from ..rules.earthbend_shapes import earthbend_descriptor_dependencies
        return earthbend_descriptor_dependencies(value)
    if value.get("op") == SUSPECT_OPERATION:
        from ..rules.suspect_shapes import suspect_descriptor_dependencies
        return suspect_descriptor_dependencies(value)
    from ..rules.qualified_destruction import qualified_destruction_payload_dependencies
    destruction = qualified_destruction_payload_dependencies(value)
    if value.get("op") == "destroy_all" and "followup" in value:
        from ..rules.destruction_result_model import DestructionFollowup

        return (*destruction, *DestructionFollowup.from_dict(value["followup"]).capability_dependencies)
    if destruction:
        return destruction
    if value.get("op") == "move_public_zone_set" and "followup" in value:
        from ..public_zone_moves import PublicZoneMoveSetSpec
        from ..rules.public_exile_result_model import PublicExileFollowup

        followup = PublicExileFollowup.from_dict(value["followup"])
        followup.validate_move(PublicZoneMoveSetSpec.from_dict(value["set"]))
        return followup.capability_dependencies
    from ..rules.revealed_library_top import REVEAL_TOP_OPERATION, RevealedLibraryTopSpec
    if value.get("op") == REVEAL_TOP_OPERATION:
        return RevealedLibraryTopSpec.from_effect(value).capability_dependencies
    return ()


def _damage_payload_dependencies(value, mechanics):
    from ..announced_damage import (
        ANNOUNCED_DAMAGE_CAPABILITY, ANNOUNCED_DAMAGE_EFFECT_FIELD, VariableDamageAllocationSpec,
        validate_announced_damage_effect,
    )
    from ..damage_death_exile_model import damage_death_exile_payload_dependencies
    from ..damage_prevention_permission import unpreventability_payload_dependencies

    if ANNOUNCED_DAMAGE_EFFECT_FIELD in value:
        spec = validate_announced_damage_effect(value)
        return spec.capability_dependencies if isinstance(spec, VariableDamageAllocationSpec) else (ANNOUNCED_DAMAGE_CAPABILITY,)
    return (*unpreventability_payload_dependencies(value, coverage=mechanics),
            *damage_death_exile_payload_dependencies(value, coverage=mechanics))


def _zone_choice_payload_dependencies(value):
    from ..apnap_object_payment import apnap_object_payment_dependencies
    from ..library_search_model import counted_search_bound_dependencies
    from ..reveal_until_library_selection_model import reveal_until_selection_payload_dependencies
    from ..variable_library_selection_model import variable_library_selection_payload_dependencies

    return (*apnap_object_payment_dependencies(value),
            *counted_search_bound_dependencies(value),
            *variable_library_selection_payload_dependencies(value),
            *reveal_until_selection_payload_dependencies(value))


def _affected_quantity_payload_dependencies(
    value: Mapping[str, Any], *, mechanics: tuple[str, ...],
) -> tuple[str, ...] | None:
    from ..fixed_token_production import (
        INVESTIGATE_CAPABILITY_ID, SEQUENTIAL_INVESTIGATE_OPERATION, sequential_investigate_count,
    )
    if value.get("op") == SEQUENTIAL_INVESTIGATE_OPERATION:
        count = sequential_investigate_count(value)
        return (INVESTIGATE_CAPABILITY_ID, *typed_result_amount_runtime_dependencies(count, mechanics=mechanics))
    from ..rules.blight_model import BLIGHT_CAPABILITY, BLIGHT_OPERATION, fixed_blight_amount
    if value.get("op") == BLIGHT_OPERATION:
        fixed_blight_amount(value)
        return (BLIGHT_CAPABILITY,)
    from ..rules.endure_model import ENDURE_CAPABILITY, ENDURE_OPERATION, fixed_endure_amount
    if value.get("op") == ENDURE_OPERATION:
        fixed_endure_amount(value)
        return (ENDURE_CAPABILITY,)
    if value.get("players") == "$targets":
        from ..rules.any_number_target_shapes import selected_player_payload_dependencies
        return selected_player_payload_dependencies(value)
    if (value.get("op") == "choose_cards_apnap" and value.get("zone") == "hand"
            and value.get("then") == "exile" and value.get("predicate") is not None):
        from ..rules.affected_player_discard_capability_shapes import fixed_hand_exile_payload_dependencies
        return fixed_hand_exile_payload_dependencies(value)
    from ..library_order_model import is_affected_hand_top_choice
    if is_affected_hand_top_choice(value):
        from ..rules.affected_player_discard_capability_shapes import fixed_hand_top_apnap_payload_dependencies
        return fixed_hand_top_apnap_payload_dependencies(value)
    if value.get("op") == "mill" and isinstance(value.get("count"), Mapping):
        from ..rules.mill_capability_shapes import half_mill_payload_dependencies
        return half_mill_payload_dependencies(value) or None
    return None


def _scalar_amount_payload_dependencies(value: Mapping[str, Any]) -> tuple[str, ...]:
    from ..cast_event_amounts import CAST_MANA_VALUE_CAPABILITY
    from ..query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY
    from ..reflexive_trigger_model import SELECTED_REFLEXIVE_SACRIFICE_CAPABILITY
    from ..scalar_effect_amount_model import (
        PAID_SACRIFICE_MANA_VALUE_CAPABILITY, PAID_SACRIFICE_STAT_CAPABILITY,
        SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec,
    )

    spec = ScalarEffectAmountSpec.from_dict(value)
    origin_capabilities = {
        ScalarAmountOrigin.REFLEXIVE_PAID_CREATURE: SELECTED_REFLEXIVE_SACRIFICE_CAPABILITY,
        ScalarAmountOrigin.PAID_SACRIFICE_CREATURE: PAID_SACRIFICE_STAT_CAPABILITY,
        ScalarAmountOrigin.PAID_SACRIFICE_MANA_VALUE: PAID_SACRIFICE_MANA_VALUE_CAPABILITY,
        ScalarAmountOrigin.CAST_SPELL_MANA_VALUE: CAST_MANA_VALUE_CAPABILITY,
    }
    required = {SCALAR_AMOUNT_CAPABILITY, *spec.history_count_capabilities}
    if capability := origin_capabilities.get(spec.origin):
        required.add(capability)
    if spec.binding_id is not None:
        required.add(DECLARED_AMOUNT_CAPABILITY)
    return tuple(sorted(required))


def typed_result_amount_runtime_dependencies(value: Any, *, mechanics: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Decode persisted public/scalar amounts without trusting annotations."""
    from ..query_effect_amount_model import (PUBLIC_QUERY_AMOUNT_KIND, PUBLIC_QUERY_AMOUNT_CAPABILITY,
        DECLARED_AMOUNT_CAPABILITY, PublicQueryAmountSpec)
    if isinstance(value, Mapping):
        affected_quantity = _affected_quantity_payload_dependencies(value, mechanics=mechanics)
        if affected_quantity is not None:
            return affected_quantity
        from ..entry_x_amount_model import entry_x_amount_dependencies
        if value.get('kind')=='source_entry_x_amount':return entry_x_amount_dependencies(value)
        from ..rules.graveyard_self_return import graveyard_self_return_dependencies
        returned=graveyard_self_return_dependencies(value)
        if returned:return returned
        action_result = _owned_operation_payload_dependencies(value)
        if action_result:
            return action_result
        from ..alternative_discard_model import alternative_discard_runtime_dependencies
        alternative=alternative_discard_runtime_dependencies(value)
        if alternative:return tuple(sorted({*alternative,*(cap for child in value.values()
            for cap in typed_result_amount_runtime_dependencies(child,mechanics=mechanics))}))
        if value.get('op') == 'creature_power_damage':
            from ..creature_power_damage_model import creature_power_damage_multiplier, CREATURE_POWER_DAMAGE_MULTIPLE_CAPABILITY
            if creature_power_damage_multiplier(value) == 2:
                return (CREATURE_POWER_DAMAGE_MULTIPLE_CAPABILITY,)
        if value.get('op') == 'apply_if_public_condition' and value.get('schema_version') == 12:
            from ..declared_cost_conditions import validate_declared_cost_instruction
            from ..declared_cast_cost_model import DECLARED_CAST_COST_CAPABILITY
            validate_declared_cost_instruction(value)
            return tuple(sorted({DECLARED_CAST_COST_CAPABILITY, 'resolution.effect.public_condition',
                *(cap for child in value.values() for cap in typed_result_amount_runtime_dependencies(child, mechanics=mechanics))}))
        from ..discard_result_model import DISCARD_RESULT_OPERATION, DISCARD_RESULT_CAPABILITY, validate_discard_draw
        if value.get('op') == DISCARD_RESULT_OPERATION:
            validate_discard_draw(value)
            return (DISCARD_RESULT_CAPABILITY,)
        from ..rules.untargeted_untap_shapes import untargeted_untap_payload_dependencies
        untap = untargeted_untap_payload_dependencies(value)
        if untap:return untap
        from ..rules.library_order_shapes import library_order_payload_dependencies
        library_order=library_order_payload_dependencies(value)
        if library_order:return library_order
        from ..public_history_query_model import public_history_query_dependencies
        from ..spell_history_ordinal_model import qualified_spell_ordinal_dependencies,QUALIFIED_SPELL_ORDINAL_FIELD
        from ..public_history_query_model import TOKEN_CREATION_FACT
        history = public_history_query_dependencies(value) if (
            'history_query' in value or value.get('kind') in {'history_count_at_least', 'history_count_at_most'}
            or value.get('fact') == TOKEN_CREATION_FACT
        ) else ()
        if value.get('field')==QUALIFIED_SPELL_ORDINAL_FIELD:history=(*history,*qualified_spell_ordinal_dependencies(value))
        if history:
            return tuple(sorted({*history,*(cap for child in value.values() for cap in typed_result_amount_runtime_dependencies(child,mechanics=mechanics))}))
        from ..rules.modal_node_capabilities import modal_schema_runtime_dependencies
        modal=modal_schema_runtime_dependencies(value)
        if modal:
            return tuple(sorted({*modal,*(cap for child in value.values() for cap in typed_result_amount_runtime_dependencies(child,mechanics=mechanics))}))
        from ..rules.all_kind_counter_shapes import all_kind_counter_payload_dependencies
        all_kinds=all_kind_counter_payload_dependencies(value)
        if all_kinds:
            return tuple(sorted({*all_kinds,*(cap for child in value.values() for cap in typed_result_amount_runtime_dependencies(child,mechanics=mechanics))}))
        from ..rules.counter_target_set_shapes import quantified_counter_payload_dependencies
        counter_targets=quantified_counter_payload_dependencies(value)
        if counter_targets:
            return tuple(sorted({*counter_targets,*(cap for child in value.values() for cap in typed_result_amount_runtime_dependencies(child,mechanics=mechanics))}))
        from ..target_count_cost_model import TARGET_COUNT_COST_HANDLER, TARGET_COUNT_COST_CAPABILITY, FixedManaPerTargetCostSpec
        if value.get('handler_id') == TARGET_COUNT_COST_HANDLER:
            FixedManaPerTargetCostSpec.from_dict(value['target_cost'])
            return (TARGET_COUNT_COST_CAPABILITY,)
        from ..target_cardinality import SELECTED_TARGET_COUNT_KIND, ANY_NUMBER_TARGET_CAPABILITY, validate_selected_target_count
        if value.get('kind') == SELECTED_TARGET_COUNT_KIND:
            validate_selected_target_count(value)
            return (ANY_NUMBER_TARGET_CAPABILITY,)
        from ..static_player_restriction_model import static_player_restriction_capabilities
        own = static_player_restriction_capabilities(value) if 'player_restriction' in value else ()
        from ..life_gain_permission import life_gain_prohibition_payload_dependencies
        own = (*own,*life_gain_prohibition_payload_dependencies(value,coverage=mechanics))
        from ..counter_permission import counter_prohibition_payload_dependencies
        own = (*own,*counter_prohibition_payload_dependencies(value,coverage=mechanics))
        own = (*own, *_damage_payload_dependencies(value, mechanics))
        from ..zone_trigger_events import counter_destination_payload_dependencies
        own = (*own, *counter_destination_payload_dependencies(value))
        if value.get('kind') == 'legend_rule_exemption':
            from ..legend_rule_model import legend_rule_payload_dependencies
            own = (*own, *legend_rule_payload_dependencies(value))
        from ..rules.casting_additional_cost_groups import selected_zone_cost_payload_dependencies
        own = (*own, *selected_zone_cost_payload_dependencies(value))
        from ..fixed_effect_payment import fixed_payment_payload_dependencies
        own = (*own, *fixed_payment_payload_dependencies(value))
        from ..resolution_mana_model import resolution_mana_choice_dependencies
        own = (*own, *resolution_mana_choice_dependencies(value))
        from ..created_token_counter_model import created_token_counter_payload_dependencies
        own = (*own, *created_token_counter_payload_dependencies(value))
        from ..rules.attachment_actions import created_token_attachment_payload_dependencies
        own = (*own, *created_token_attachment_payload_dependencies(value))
        from ..incubate_model import incubate_payload_dependencies
        own = (*own,*incubate_payload_dependencies(value))
        from ..discover_model import discover_payload_dependencies
        own = (*own,*discover_payload_dependencies(value))
        own = (*own, *_zone_choice_payload_dependencies(value))
        from ..library_battlefield_model import library_battlefield_payload_dependencies
        own = (*own,*library_battlefield_payload_dependencies(value))
        from ..public_zone_quantity_model import public_zone_quantity_dependencies
        own = (*own, *public_zone_quantity_dependencies(value))
        from ..prevention_recipient_sets import prevention_recipient_set_dependencies
        own = (*own, *prevention_recipient_set_dependencies(value))
        from ..attached_prevention_model import attached_prevention_dependencies
        own = (*own, *attached_prevention_dependencies(value))
        from ..attachment_references import attachment_information_payload_dependencies
        own = (*own, *attachment_information_payload_dependencies(value))
        from ..ability_source_information import ability_source_filter_dependencies
        own = (*own, *ability_source_filter_dependencies(value))
        from ..designation_prevention import designation_predicate_dependencies
        own = (*own, *designation_predicate_dependencies(value))
        from ..rules.token_creation_capability_shapes import token_definition_payload_dependencies
        own = (*own, *token_definition_payload_dependencies(value,mechanics))
        from ..rules.attacking_token_results import attacking_token_payload_dependencies
        own = (*own, *attacking_token_payload_dependencies(value))
        from ..rules.created_token_cleanup_shapes import created_token_cleanup_payload_dependencies
        own = (*own, *created_token_cleanup_payload_dependencies(value))
        from ..source_prevention import source_prevention_dependencies
        own = (*own, *source_prevention_dependencies(value))
        from ..conditional_recipient_model import conditional_recipient_capabilities
        own = (*own, *conditional_recipient_capabilities(value))
        from ..public_zone_condition_model import public_zone_condition_capabilities
        own = (*own, *public_zone_condition_capabilities(value))
        from ..untap_step import CONDITIONAL_UNTAP_HANDLER_ID
        if value.get("handler_id") == CONDITIONAL_UNTAP_HANDLER_ID:
            condition = value.get("public_condition")
            if isinstance(condition, Mapping) and condition.get("scope") == "subject_controller_not_monarch":
                own = (*own, "variant.monarch.designate")
        from ..rules.conditional_free_cast_shapes import conditional_free_cast_capabilities
        own = (*own, *conditional_free_cast_capabilities(value))
        from ..land_entry_condition_model import land_entry_condition_capabilities
        own = (*own, *land_entry_condition_capabilities(value))
        from ..nonmana_kicker import NONMANA_KICKER_HANDLER,FixedNonmanaKickerSpec,nonmana_kicker_dependencies
        if value.get('handler_id')==NONMANA_KICKER_HANDLER:
            own=(*own,*nonmana_kicker_dependencies(FixedNonmanaKickerSpec.from_dict(value['kicker'])))
        if value.get('kind') == 'public_quantity_mana':
            from ..public_quantity_mana_model import PublicQuantityManaOutput, PUBLIC_QUANTITY_MANA_CAPABILITY
            PublicQuantityManaOutput.from_dict(value)
            return (PUBLIC_QUANTITY_MANA_CAPABILITY,)
        if value.get('kind') == PUBLIC_QUERY_AMOUNT_KIND:
            spec = PublicQueryAmountSpec.from_dict(value)
            return tuple(sorted({*own,PUBLIC_QUERY_AMOUNT_CAPABILITY,
                                 *((DECLARED_AMOUNT_CAPABILITY,) if spec.schema_version == 2 else ())}))
        if value.get('kind') == 'scalar_effect_amount':
            return _scalar_amount_payload_dependencies(value)
        return tuple(sorted({*own, *(cap for child in value.values() for cap in typed_result_amount_runtime_dependencies(child,mechanics=mechanics))}))
    if isinstance(value, (list, tuple)):
        return tuple(sorted({cap for child in value for cap in typed_result_amount_runtime_dependencies(child,mechanics=mechanics)}))
    return ()
