from __future__ import annotations

"""Existing typed result leaves bound to one normalized trigger occurrence."""

from .target_announcement_bindings import target_announcement_result, target_announcement_bound_result
from .tap_state_event_bindings import tap_state_bound_result
from .scalar_effect_amounts import scalar_effect_amount_template
from .token_copy_templates import token_copy_recipe_template
from .fixed_source_combat_growth import fixed_source_combat_growth_effect_template
from .fixed_entry_return_requirements import fixed_entry_return_effect_template
from .endure_templates import binding_endure_trigger_result


def binding_effect_template(binding, body: str, *, card_name: str, effect_template, _allow_payment: bool = True, source_counter_variants=(), _allow_optional: bool = True):
    if _allow_payment:
        from .source_sacrifice_payments import normalized_source_sacrifice_payment_text
        body=normalized_source_sacrifice_payment_text(binding,body,card_name=card_name)
    endure = binding_endure_trigger_result(binding, body, source_name=card_name, effect_template=effect_template)
    if endure is not None:
        return endure, False
    from .event_player_target_results import event_player_target_result
    defending_target=event_player_target_result(binding,body,compile_result=lambda text: binding_effect_template(
        binding,text,card_name=card_name,effect_template=effect_template,_allow_payment=_allow_payment,
        source_counter_variants=source_counter_variants,_allow_optional=_allow_optional))
    if defending_target is not None:return defending_target
    return _binding_result_template(binding,body,card_name=card_name,effect_template=effect_template,
        _allow_payment=_allow_payment,source_counter_variants=source_counter_variants,_allow_optional=_allow_optional)


def _binding_result_template(binding, body: str, *, card_name: str, effect_template, _allow_payment: bool = True, source_counter_variants=(), _allow_optional: bool = True):
    from .graveyard_self_return import graveyard_self_return_result
    returned=graveyard_self_return_result(binding,body,card_name=card_name)
    if returned is not None:return returned
    from .delayed_object_templates import delayed_object_result
    delayed=delayed_object_result(binding,body,card_name=card_name)
    if delayed is not None:return delayed
    from .source_counter_quantity_sequences import source_bound_sequence_result
    source_sequence=source_bound_sequence_result(binding,body,card_name=card_name,effect_template=effect_template)
    if source_sequence is not None:return source_sequence
    from .source_self_effect_templates import source_cycled_optional_damage_result
    cycled_damage = source_cycled_optional_damage_result(binding, body) if _allow_optional and _allow_payment else None
    if cycled_damage is not None:
        return cycled_damage, False
    from .counter_removal_templates import fixed_source_counter_removal_effect_template, all_named_source_counter_removal_effect_template
    source_pronoun = binding.event.value.endswith('.self') or binding.variant in {
            'source_attacks', 'this_creature_blocks', 'this_creature_becomes_blocked',
            'source_dealt_damage', 'source_received_combat_damage',
        }
    if binding.active_zone == 'battlefield' and _allow_optional and _allow_payment:
        for compile_removal in (fixed_source_counter_removal_effect_template, all_named_source_counter_removal_effect_template):
            source_removal = compile_removal(body, card_name=card_name, allow_source_pronoun=source_pronoun)
            if source_removal is not None:
                return source_removal, False
    if _allow_optional:
        from .binding_optional_results import binding_aware_optional_result
        optional = binding_aware_optional_result(body, compile_child=lambda text: binding_effect_template(binding,text,card_name=card_name,effect_template=effect_template,_allow_payment=_allow_payment,source_counter_variants=source_counter_variants,_allow_optional=False))
        if optional is not None:return optional
    from ..expend_model import EXPEND_CAPABILITY
    if EXPEND_CAPABILITY in binding.explicit_capabilities:
        from .source_self_effect_templates import normalized_source_result_body
        normalized=normalized_source_result_body(body)
        if normalized is not None:body=normalized
    from .fixed_stack_spell_copy import cast_event_spell_copy_template
    cast_copy=cast_event_spell_copy_template(binding,body)
    if cast_copy is not None:return cast_copy
    from .attack_event_results import qualified_attack_characteristic_result
    attack_result=qualified_attack_characteristic_result(binding,body,effect_template=effect_template,card_name=card_name)
    if attack_result is not None:return attack_result
    from .occurrence_object_results import occurrence_object_result
    occurrence=occurrence_object_result(binding,body,effect_template=effect_template,card_name=card_name)
    if occurrence is not None:return occurrence
    from .event_counter_results import public_event_counter_set_result
    counter_set=public_event_counter_set_result(binding,body,effect_template=effect_template,card_name=card_name,accepted_variants=source_counter_variants)
    if counter_set is not None:return counter_set
    from .event_counter_results import public_event_source_counter_result
    source_counter = public_event_source_counter_result(
        binding, body, effect_template=effect_template, card_name=card_name,
        accepted_variants=source_counter_variants,
        allow_source_pronoun=source_pronoun,
    )
    if source_counter is not None:return source_counter
    from .event_counter_results import public_event_target_counter_result
    target_counter = public_event_target_counter_result(
        binding, body, effect_template=effect_template, card_name=card_name,
        accepted_variants=source_counter_variants,
    )
    if target_counter is not None:return target_counter
    from .event_counter_results import entry_event_counter_result
    counter=entry_event_counter_result(binding,body,effect_template=effect_template,card_name=card_name)
    if counter is not None:return counter
    from .event_player_results import fixed_event_player_result
    player_result=fixed_event_player_result(binding,body,effect_template=effect_template,card_name=card_name)
    if player_result is not None:return player_result
    from .event_player_results import fixed_event_player_damage_result
    player_damage=fixed_event_player_damage_result(binding,body,effect_template=effect_template,card_name=card_name)
    if player_damage is not None:return player_damage
    from .scheduled_turn_skip_templates import damage_player_turn_skip_result
    skip = damage_player_turn_skip_result(binding, body)
    if skip is not None:
        return skip
    from .event_player_choice_templates import fixed_event_player_sacrifice_result
    player_choice = fixed_event_player_sacrifice_result(binding, body,
        effect_template=effect_template, card_name=card_name)
    if player_choice is not None:return player_choice
    from .mill_templates import event_recipient_mill_result
    mill=event_recipient_mill_result(binding,body,compile_fixed=lambda text:effect_template(text,card_name=card_name),source_name=card_name)
    if mill is not None:return mill
    if binding.event.value.endswith('.self') or binding.variant in {'source_attacks', 'this_creature_blocks', 'this_creature_becomes_blocked', 'source_dealt_damage', 'source_received_combat_damage'}:
        from .explore_templates import single_explore_effect_template
        explored = single_explore_effect_template(body, allow_source_pronoun=True)
        if explored is not None:return explored.compiled(), False
        from .damage_templates import source_pronoun_damage_effect_template
        damage=source_pronoun_damage_effect_template(body)
        if damage is not None:return damage.compiled(),False
        from .connive_templates import fixed_connive_effect_template
        connive = fixed_connive_effect_template(body, source_name=card_name, source_is_permanent=True,
                                                allow_source_pronoun=True)
        if connive is not None:return connive, False
    if _allow_payment:
        from .optional_payment_templates import binding_aware_optional_mana_payment_template
        payment = binding_aware_optional_mana_payment_template(
            body,
            compile_result=lambda text: binding_effect_template(
                binding, text, card_name=card_name,
                effect_template=effect_template, _allow_payment=False,
                source_counter_variants=source_counter_variants,
            ),
        )
        if payment is not None:
            return payment
    from .affected_player_discard_templates import damage_player_discard_result,defending_player_discard_result
    defending_discard=defending_player_discard_result(binding,body)
    if defending_discard is not None:return defending_discard
    discard = damage_player_discard_result(binding, body)
    if discard is not None:
        return discard
    from ..random_discard import damage_player_random_discard_result
    random_discard = damage_player_random_discard_result(binding, body)
    if random_discard is not None:
        return random_discard
    specialized = fixed_source_combat_growth_effect_template(body, event=binding.event.value, variant=binding.variant)
    if specialized[0] is not None:return specialized,True
    if binding.event.value.endswith('.self') or binding.variant in {'source_attacks','this_creature_blocks','this_creature_becomes_blocked','source_public_tap_state_transition','source_dealt_damage'}:
        from .source_self_effect_templates import normalized_source_result_body, pin_source_result_incarnation, _source_self_counter_payment
        payment=_source_self_counter_payment(body,card_name=card_name,compile_effect=effect_template)
        if payment is not None:return payment,True
        normalized=normalized_source_result_body(body)
        if normalized is not None:
            value=effect_template(normalized,card_name=card_name)
            if value[0] is not None:return pin_source_result_incarnation(value),True
    from .scheduled_player_triggers import scheduled_player_result
    scheduled=scheduled_player_result(binding,body,effect_template=effect_template,card_name=card_name)
    if scheduled is not None:return scheduled
    from .event_card_return_templates import event_card_return_template
    returned = event_card_return_template(binding, body, card_name=card_name)
    if returned is not None:
        return returned, False
    sacrifice = target_announcement_bound_result(binding, body, card_name=card_name)
    if sacrifice is not None:
        return sacrifice, False
    body = target_announcement_result(binding, body, card_name=card_name)
    event_result = tap_state_bound_result(binding, body, effect_template=effect_template, card_name=card_name)
    if event_result is not None:
        return event_result
    scalar = scalar_effect_amount_template(
        body, source_name=card_name, event=binding.event.value,
        source_event=binding.variant.startswith('source_'),
        compile_fixed=lambda text: effect_template(text, card_name=card_name),
    )
    if scalar is not None:
        return scalar, False
    copy_recipe = token_copy_recipe_template(body, source_name=card_name, source_is_permanent=True, event=binding.event.value)
    if copy_recipe is not None:
        return copy_recipe.compiled(), False
    if binding.variant == 'fixed_entry_return_requirement':
        entry_return = fixed_entry_return_effect_template(body)
        if entry_return[0] is not None:
            return entry_return, True
    return effect_template(body, card_name=card_name), False
