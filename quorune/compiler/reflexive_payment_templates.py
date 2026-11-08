from __future__ import annotations

"""One represented fixed action followed by its separate reflexive body."""

import hashlib
import re

from ..creature_power_damage_model import CREATURE_POWER_DAMAGE_OPERATION, occurrence_power_damage_in
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, REFLEXIVE_PAYMENT_MECHANIC, ReflexiveTriggerProgram,
)
from ..util import stable_json
from .fixed_effect_payment_templates import fixed_effect_payment_spec
from .source_self_effect_templates import pin_source_result_incarnation


def reflexive_payment_template(text, *, compile_effect, bound_trigger=False):
    match = re.fullmatch(r'You may (?P<cost>.+?)\. When you do, (?P<body>.+)', text.strip(), re.I)
    if match is None:
        return None
    payment = fixed_effect_payment_spec(match['cost'])
    if (payment is None or bound_trigger and payment.kind not in {'mana', 'energy'}
            or re.search(r'\b(?:when you|if you do|this way)\b', match['body'], re.I)):
        return None
    compiled = compile_effect(match['body'])
    if compiled[0] is None:
        return None
    compiled = pin_source_result_incarnation(compiled)
    effects = compiled[1]
    occurrence = any(occurrence_power_damage_in(effects))
    if occurrence and not bound_trigger:
        return None
    if re.search(r'\bits (?:power|toughness)\b', match['body'], re.I) and not (
        bound_trigger and len(effects) == 1 and effects[0].get('op') == CREATURE_POWER_DAMAGE_OPERATION
    ):
        return None
    try:
        program = ReflexiveTriggerProgram(
            effects=effects, target_schema=compiled[2], mechanic_ids=compiled[3],
            schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION if bound_trigger else 1,
        )
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


def binding_aware_reflexive_payment_template(text, *, compile_result):
    requires_current_ability = False

    def compile_child(body):
        nonlocal requires_current_ability
        compiled, requires_current_ability = compile_result(body)
        return compiled

    compiled = reflexive_payment_template(text, compile_effect=compile_child, bound_trigger=True)
    return (compiled, requires_current_ability) if compiled is not None else None
