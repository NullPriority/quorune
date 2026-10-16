from __future__ import annotations

"""One represented fixed action followed by its separate reflexive body."""

import hashlib
import re

from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
from ..reflexive_trigger_model import REFLEXIVE_PAYMENT_MECHANIC, ReflexiveTriggerProgram
from ..util import stable_json
from .fixed_effect_payment_templates import fixed_effect_payment_spec
from .source_self_effect_templates import pin_source_result_incarnation


def reflexive_payment_template(text, *, compile_effect):
    match = re.fullmatch(r'You may (?P<cost>.+?)\. When you do, (?P<body>.+)', text.strip(), re.I)
    if match is None:
        return None
    payment = fixed_effect_payment_spec(match['cost'])
    if payment is None or re.search(r'\b(?:when you|if you do|this way|its power|its toughness)\b', match['body'], re.I):
        return None
    compiled = compile_effect(match['body'])
    if compiled[0] is None:
        return None
    compiled = pin_source_result_incarnation(compiled)
    try:
        program = ReflexiveTriggerProgram(effects=compiled[1], target_schema=compiled[2], mechanic_ids=compiled[3])
    except (TypeError, ValueError, KeyError):
        return None
    from ..rules.reflexive_payment_shapes import reflexive_program_capabilities
    if not reflexive_program_capabilities(program):
        return None
    effect = {'op': 'offer_optional_mana_payment', 'schema_version': 4, 'player': '$controller',
        'payment': payment.to_dict(), 'reflexive_program': program.to_dict()}
    if payment.kind == 'mana':
        effect['cost'] = dict(payment.requirements)
    identity = hashlib.sha256(stable_json(effect).encode('utf-8')).hexdigest()[:16]
    return ('fixed-reflexive-payment-' + identity + '-v1', (effect,), None,
        (FIXED_EFFECT_PAYMENT_MECHANIC, REFLEXIVE_PAYMENT_MECHANIC))
