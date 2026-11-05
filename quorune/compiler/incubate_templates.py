from __future__ import annotations

"""Incubate counts remain one instruction, including two ordered incubations."""

import re

from ..incubate_model import INCUBATE_OPERATION,INCUBATE_MECHANIC
from .declared_effect_amounts import declared_effect_amount_template
from .scalar_effect_amounts import scalar_effect_amount_template


def incubate_effect_template(text,*,source_name,cast_x_available=False,forbid_public_x=False,**context):
    def fixed(body):
        match = re.fullmatch(r'Incubate (?P<amount>0|[1-9][0-9]*)(?P<twice> twice)?\.?',body.strip(),re.I)
        if match is None:return None,(),None,()
        effect = {'op':INCUBATE_OPERATION,'schema_version':1,'controller':'$controller','source':'$source',
            'amount':int(match['amount']),'repetitions':2 if match['twice'] else 1}
        return 'incubate-v1',(effect,),None,(INCUBATE_MECHANIC,'cr-111-tokens')
    result = fixed(text)
    if result[0] is not None:return result
    return declared_effect_amount_template(text,source_name=source_name,compile_fixed=fixed,
        cast_x_available=cast_x_available,forbid_public_x=forbid_public_x) or scalar_effect_amount_template(
            text,source_name=source_name,compile_fixed=fixed,event=context.get('event'),source_event=context.get('source_event',False))
