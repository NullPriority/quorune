from __future__ import annotations

from . import regex_working_set as re
from ..created_token_counter_model import (
    ANNOUNCED_X_CREATE_AND_WIPE, CREATE_THEN_COUNTER, CREATED_TOKEN_COUNTER_MECHANIC,
    CREATED_TOKEN_WIPE_MECHANIC, validate_announced_x_token_wipe,
)
from ..query_effect_amount_model import CastXAmountSpec
from .token_templates import fixed_token_creation_effect_template
from .fixed_numbers import fixed_number
from .declared_effect_amounts import DECLARED_EFFECT_AMOUNT_MECHANIC, declared_effect_amount_template
from .scalar_effect_amounts import scalar_effect_amount_template


def _announced_x_token_wipe_template(text):
    match = re.fullmatch(r'You gain X life\. Create X (?P<token>.+?) If X is (?P<minimum>[1-9][0-9]*) '
        r'or more, destroy all other creatures\.?', ' '.join(text.split()), re.I)
    if match is None:
        return None
    body = match['token']
    if not body.endswith(('.', '."', '.”')):
        return None
    token = fixed_token_creation_effect_template('Create one ' + body)
    if token is None or token.target_schema is not None:
        return None
    effect = {'op': ANNOUNCED_X_CREATE_AND_WIPE, 'schema_version': 1, 'controller': '$controller',
        'source': '$source', 'amount': CastXAmountSpec().to_dict(), 'minimum': int(match['minimum']),
        'token': dict(token.effect), 'token_mechanics': list(token.mechanics)}
    try:
        validate_announced_x_token_wipe(effect)
    except (TypeError, ValueError, KeyError):
        return None
    return ('announced-x-created-token-destruction-v1', (effect,), None,
        (CREATED_TOKEN_WIPE_MECHANIC, DECLARED_EFFECT_AMOUNT_MECHANIC, *token.mechanics))


def announced_x_token_wipe_spell_compiler(fallback):
    def compile_token_wipe(text, **context):
        return _announced_x_token_wipe_template(text) or fallback(text, **context)

    return compile_token_wipe


def created_token_counter_effect_template(text,*,source_name,cast_x_available=False,forbid_public_x=False,**context):
    if cast_x_available and context.get('source_is_permanent') is False:
        if wipe := _announced_x_token_wipe_template(text):
            return wipe
    match=re.fullmatch(r'(?P<creation>Create .+? tokens?(?: with [^.]+?)?)(?:\.\s*|,?\s+(?:and|then)\s+)(?P<counter>Put .+)',text.strip(),re.I)
    if match is None:return None
    token=fixed_token_creation_effect_template(match['creation'].rstrip(' ,')+'.')
    if token is None or token.target_schema is not None or token.effect.get('quantity')!=1:return None
    def fixed(body):
        counter=re.fullmatch(r'Put (?P<amount>a|one|two|three|four|five|six|seven|eight|nine|ten|[0-9]+) \+1/\+1 counters? on it\.?',body.strip(),re.I)
        if counter is None:return (None,(),None,())
        effect={'op':CREATE_THEN_COUNTER,'schema_version':1,'controller':'$controller','source':'$source',
            'token':dict(token.effect),'token_mechanics':list(token.mechanics),'amount':fixed_number(counter['amount'])}
        return 'created-token-counter-followup-v1',(effect,),None,(CREATED_TOKEN_COUNTER_MECHANIC,*token.mechanics)
    body=match['counter']
    equal=re.fullmatch(r'Put a number of \+1/\+1 counters on it equal to (?P<value>.+?)\.?',body,re.I)
    if equal is not None:body='Put X +1/+1 counters on it, where X is '+equal['value']+'.'
    first=fixed(body)
    if first[0] is not None:return first
    return declared_effect_amount_template(body,source_name=source_name,compile_fixed=fixed,
        cast_x_available=cast_x_available,forbid_public_x=forbid_public_x) or scalar_effect_amount_template(
            body,source_name=source_name,compile_fixed=fixed,event=context.get('event'),source_event=context.get('source_event',False))
