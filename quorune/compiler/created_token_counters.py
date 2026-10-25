from __future__ import annotations

import re
from ..created_token_counter_model import CREATE_THEN_COUNTER,CREATED_TOKEN_COUNTER_MECHANIC
from .token_templates import fixed_token_creation_effect_template
from .fixed_numbers import fixed_number
from .declared_effect_amounts import declared_effect_amount_template
from .scalar_effect_amounts import scalar_effect_amount_template


def created_token_counter_effect_template(text,*,source_name,cast_x_available=False,forbid_public_x=False,**context):
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
