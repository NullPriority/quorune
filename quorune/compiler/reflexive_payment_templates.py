from __future__ import annotations

"""One represented fixed action followed by its separate reflexive body."""

import hashlib
import re

from ..creature_power_damage_model import CREATURE_POWER_DAMAGE_OPERATION, occurrence_power_damage_in
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, REFLEXIVE_PAYMENT_MECHANIC,
    SELECTED_SACRIFICE_REFLEXIVE_PROGRAM_VERSION, ReflexiveTriggerProgram,
)
from ..creature_subtypes import canonical_creature_subtype
from ..scalar_effect_amount_model import SCALAR_AMOUNT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec
from ..util import stable_json
from .fixed_effect_payment_templates import fixed_effect_payment_spec
from .source_self_effect_templates import pin_source_result_incarnation


_PAID_POWER_DAMAGE = re.compile(
    r"(?P<source>[^.]+?) deals damage equal to (?:that creature['\u2019]s|the sacrificed creature['\u2019]s)"
    r" power to (?P<target>any target|target creature)"
    r"(?: and you (?P<tokens>create [^.]+ tokens?))?\."
    r"(?: If the sacrificed creature was a (?P<subtype>[^.]+?), (?P=source) deals twice that much damage instead\.)?",
    re.I,
)


def _compile_paid_power_result(body, compile_effect):
    match = _PAID_POWER_DAMAGE.fullmatch(body)
    if match is None:
        return None
    subtype = canonical_creature_subtype(match['subtype'])
    if match['subtype'] is not None and subtype is None:
        return None
    text = f"{match['source']} deals 1 damage to {match['target']}."
    if match['tokens']:
        text += f" You {match['tokens']}."
        from .fixed_effect_clause_sequences import fixed_effect_clause_sequence_template
        sequence = fixed_effect_clause_sequence_template(text, compile_clause=compile_effect)
        compiled = sequence.compiled() if sequence is not None else (None, (), None, ())
    else:
        compiled = compile_effect(text)
    template, effects, schema, mechanics = compiled
    if (template is None or schema is None or not effects or effects[0].get('op') != 'damage'
            or effects[0].get('source') != '$source' or effects[0].get('amount') != 1):
        return None
    amount = ScalarEffectAmountSpec(ScalarAmountOrigin.REFLEXIVE_PAID_CREATURE, characteristic='power',
                                    schema_version=11, double_for_subtype=subtype)
    damage = {**effects[0], 'amount': amount.to_dict()}
    return template, (damage, *effects[1:]), schema, (*mechanics, SCALAR_AMOUNT_MECHANIC)


def reflexive_payment_template(text, *, compile_effect, bound_trigger=False):
    match = re.fullmatch(r'You may (?P<cost>.+?)\. When you do, (?P<body>.+)', text.strip(), re.I)
    if match is None:
        return None
    payment = fixed_effect_payment_spec(match['cost'])
    if (payment is None or bound_trigger and payment.kind not in {'mana', 'energy', 'sacrifice'}
            or re.search(r'\b(?:when you|if you do|this way)\b', match['body'], re.I)):
        return None
    selected_sacrifice = bound_trigger and payment.kind == 'sacrifice'
    paid_result = _compile_paid_power_result(match['body'], compile_effect) if selected_sacrifice else None
    compiled = paid_result or compile_effect(match['body'])
    if compiled[0] is None:
        return None
    compiled = pin_source_result_incarnation(compiled)
    effects = compiled[1]
    occurrence = any(occurrence_power_damage_in(effects))
    if occurrence and (not bound_trigger or selected_sacrifice):
        return None
    if re.search(r'\bits (?:power|toughness)\b', match['body'], re.I) and not (
        bound_trigger and len(effects) == 1 and effects[0].get('op') == CREATURE_POWER_DAMAGE_OPERATION
    ):
        return None
    version = BOUND_REFLEXIVE_PROGRAM_VERSION if bound_trigger else 1
    if selected_sacrifice:
        version = SELECTED_SACRIFICE_REFLEXIVE_PROGRAM_VERSION
    try:
        program = ReflexiveTriggerProgram(
            effects=effects, target_schema=compiled[2], mechanic_ids=compiled[3],
            schema_version=version,
        )
    except (TypeError, ValueError, KeyError):
        return None
    from ..rules.reflexive_payment_shapes import reflexive_payment_parts, reflexive_program_capabilities
    if not reflexive_program_capabilities(program):
        return None
    effect = {'op': 'offer_optional_mana_payment', 'schema_version': 4, 'player': '$controller',
        'payment': payment.to_dict(), 'reflexive_program': program.to_dict()}
    if payment.kind == 'mana':
        effect['cost'] = dict(payment.requirements)
    try:
        reflexive_payment_parts(effect)
    except (TypeError, ValueError, KeyError):
        return None
    identity = hashlib.sha256(stable_json(effect).encode('utf-8')).hexdigest()[:16]
    return ('fixed-reflexive-payment-' + identity + '-v1', (effect,), None,
        (FIXED_EFFECT_PAYMENT_MECHANIC, REFLEXIVE_PAYMENT_MECHANIC))


def binding_aware_reflexive_payment_template(text, *, compile_result):
    requires_current_ability = False

    def compile_child(body):
        nonlocal requires_current_ability
        compiled, child_requires_current_ability = compile_result(body)
        if compiled[0] is not None:
            requires_current_ability |= child_requires_current_ability
        return compiled

    compiled = reflexive_payment_template(text, compile_effect=compile_child, bound_trigger=True)
    return (compiled, requires_current_ability) if compiled is not None else None
