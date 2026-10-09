from __future__ import annotations

"""Existing typed result leaves bound to one normalized trigger occurrence."""

from .target_announcement_bindings import target_announcement_result, target_announcement_bound_result
from .tap_state_event_bindings import tap_state_bound_result
from .scalar_effect_amounts import scalar_effect_amount_template
from .token_copy_templates import token_copy_recipe_template
from .fixed_source_combat_growth import fixed_source_combat_growth_effect_template
from .fixed_entry_return_requirements import fixed_entry_return_effect_template


def binding_effect_template(binding, body: str, *, card_name: str, effect_template):
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
    specialized = fixed_source_combat_growth_effect_template(body, event=binding.event.value, variant=binding.variant)
    if specialized[0] is not None:
        return specialized, True
    if binding.variant == 'fixed_entry_return_requirement':
        entry_return = fixed_entry_return_effect_template(body)
        if entry_return[0] is not None:
            return entry_return, True
    return effect_template(body, card_name=card_name), False
