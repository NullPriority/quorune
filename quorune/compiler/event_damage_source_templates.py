from __future__ import annotations

"""Damage from the proved creature occurrence, without changing ability ownership."""

from . import regex_working_set as re
from ..event_damage_source_model import EventDamageSourceReference, EVENT_DAMAGE_SOURCE_EVENTS
from .scalar_effect_amounts import trigger_scalar_effect_amount_template
from .optional_effect_templates import FixedOptionalEffectTemplate, FIXED_OPTIONAL_EFFECT_MECHANIC


def event_damage_source_template(binding, body, *, card_name, effect_template, allow_optional=True):
    event = binding.event.value
    if (binding.active_zone != 'battlefield' or event not in EVENT_DAMAGE_SOURCE_EVENTS
            or binding.variant.startswith('source_')
            or 'trigger.subscription.fixed_multi_event' in binding.explicit_capabilities):
        return None
    if event in {'damage.received', 'combat.damage.received'} and binding.variant not in {
            'received_any_creature', 'received_enchanted_creature', 'received_equipped_creature'}:
        return None
    match = re.fullmatch(r'(?P<optional>You may have )?it (?P<verb>deal|deals) '
        r'(?P<amount>[1-9]\d*|that much) damage to (?P<target>any target|each opponent|target opponent or planeswalker)\.',
        body.strip(), re.I)
    if (match is None or match['optional'] and not allow_optional
            or bool(match['optional']) != (match['verb'].casefold() == 'deal')
            or match['amount'].casefold() == 'that much' and event not in {'damage.received', 'combat.damage.received'}):
        return None
    normalized = f"{card_name} deals {match['amount']} damage to {match['target']}."
    def compile_fixed(text):
        return effect_template(text, card_name=card_name)
    compiled = trigger_scalar_effect_amount_template(normalized, binding=binding, source_name=card_name,
        compile_fixed=compile_fixed) if match['amount'].casefold() == 'that much' else compile_fixed(normalized)
    if compiled is None or compiled[0] is None or len(compiled[1]) != 1 or compiled[1][0].get('source') != '$source':
        return None
    template, effects, schema, mechanics = compiled
    effect = {**effects[0], 'source': EventDamageSourceReference(event).to_dict()}
    result = (template + '-event-dealer-v1', (effect,), schema, mechanics)
    if match['optional']:
        return FixedOptionalEffectTemplate(result[0], result[1], result[2],
            (FIXED_OPTIONAL_EFFECT_MECHANIC, *result[3])).compiled()
    return result
