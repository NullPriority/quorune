from __future__ import annotations

from typing import Mapping
from ..reflexive_trigger_model import REFLEXIVE_PAYMENT_CAPABILITY, REFLEXIVE_PAYMENT_MECHANIC, ReflexiveTriggerProgram
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec
from ..replacement.immutable import thaw_value


def reflexive_payment_parts(effect):
    fields = {'op', 'schema_version', 'player', 'payment', 'reflexive_program'}
    if (not isinstance(effect, Mapping) or set(effect) not in (fields, fields | {'cost'})
            or effect['op'] != 'offer_optional_mana_payment' or type(effect['schema_version']) is not int
            or effect['schema_version'] != 4 or type(effect['player']) is not str or not effect['player']):
        raise ValueError('Reflexive payment instructions use a closed versioned schema')
    payment = FixedEffectPaymentSpec.from_dict(effect['payment'])
    if (('cost' in effect) != (payment.kind == 'mana')
            or payment.kind == 'mana' and dict(effect['cost']) != dict(payment.requirements)):
        raise ValueError('Reflexive payment mana identity changed')
    return payment, ReflexiveTriggerProgram.from_dict(effect['reflexive_program'])


def reflexive_program_capabilities(program):
    from .fixed_effect_payment_shapes import fixed_payment_result_capabilities
    return fixed_payment_result_capabilities(effects=tuple(thaw_value(effect) for effect in program.effects),
        target_schema=thaw_value(program.target_schema) if program.target_schema is not None else None,
        mechanic_ids=program.mechanic_ids)


def reflexive_payment_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is not None or len(effects) != 1
            or not {FIXED_EFFECT_PAYMENT_MECHANIC, REFLEXIVE_PAYMENT_MECHANIC} <= set(mechanic_ids)):
        return ()
    try:
        effect = effects[0]
        payment, program = reflexive_payment_parts(effect)
        if (effect['player'] != '$controller'
                or payment.kind == 'discard' and payment.predicate.owner != '$controller'
                or payment.kind == 'sacrifice' and payment.predicate.controller != '$controller'):
            return ()
        child_capabilities = reflexive_program_capabilities(program)
        if not child_capabilities:
            return ()
        return tuple(sorted({REFLEXIVE_PAYMENT_CAPABILITY, 'trigger.placement.apnap',
            *payment.capabilities, *child_capabilities}))
    except (KeyError, TypeError, ValueError):
        return ()
