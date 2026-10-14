from __future__ import annotations

"""Existing typed result leaves bound to one normalized trigger occurrence."""

from .target_announcement_bindings import target_announcement_result, target_announcement_bound_result
from .tap_state_event_bindings import tap_state_bound_result
from .scalar_effect_amounts import scalar_effect_amount_template
from .token_copy_templates import token_copy_recipe_template
from .fixed_source_combat_growth import fixed_source_combat_growth_effect_template
from .fixed_entry_return_requirements import fixed_entry_return_effect_template


def binding_effect_template(binding, body: str, *, card_name: str, effect_template, _allow_payment: bool = True, source_counter_variants=()):
    from .event_counter_results import public_event_source_counter_result
    source_counter = public_event_source_counter_result(
        binding, body, effect_template=effect_template, card_name=card_name,
        accepted_variants=source_counter_variants,
    )
    if source_counter is not None:return source_counter
    from .event_counter_results import entry_event_counter_result
    counter=entry_event_counter_result(binding,body,effect_template=effect_template,card_name=card_name)
    if counter is not None:return counter
    from .event_player_results import fixed_event_player_result
    player_result=fixed_event_player_result(binding,body,effect_template=effect_template,card_name=card_name)
    if player_result is not None:return player_result
    from .mill_templates import event_recipient_mill_result
    mill=event_recipient_mill_result(binding,body,compile_fixed=lambda text:effect_template(text,card_name=card_name),source_name=card_name)
    if mill is not None:return mill
    if binding.event.value.endswith('.self') or binding.variant in {'source_attacks', 'this_creature_blocks', 'this_creature_becomes_blocked'}:
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
    from .affected_player_discard_templates import damage_player_discard_result
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
        from .source_self_effect_templates import normalized_source_result_body, pin_source_result_incarnation
        normalized=normalized_source_result_body(body)
        if normalized is not None and not body.casefold().startswith(('it gets','him gets','her gets')):
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
