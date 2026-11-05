"""Whole per-source-counter grammar over existing scalar characteristic results."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import re

from ..scalar_effect_amount_model import SCALAR_AMOUNT_KIND,ScalarEffectAmountSpec


def source_counter_modifier_template(text, *, source_name, compile_fixed,
                                     compile_scalar, event=None, source_event=False):
    if re.search(r'\bfor each\b',text,re.I) is None or re.search(r', where X is ',text,re.I):return None
    from .public_query_effect_amounts import _query_characteristic_candidate
    candidate=_query_characteristic_candidate(text.strip(),source_name=source_name)
    if candidate is None:return None
    subject,keywords,power,toughness,quantity=candidate
    if re.fullmatch(r'[A-Za-z0-9+/-]+ counters? on .+',quantity,re.I) is None:
        return None
    coefficients={'power':power,'toughness':toughness}
    variable=lambda value:'+0' if not value else '+X' if value>0 else '-X'
    from .fixed_public_characteristic_sets import _public_query
    public_set=_public_query(subject) is not None
    verb='get' if public_set else 'gets'
    if public_set:keywords=keywords.replace(' and gains ',' and gain ',1)
    normalized=(f'{subject} {verb} {variable(power)}/{variable(toughness)}{keywords} '
        f'until end of turn, where X is the number of {quantity}.')
    compiled=compile_scalar(normalized,source_name=source_name,compile_fixed=compile_fixed,
        event=event,source_event=source_event)
    if compiled is None:return None
    identity='scalar:x:'+hashlib.sha256(text.strip().encode('utf-8')).hexdigest()[:24]
    effects=[];modified=0
    try:
        for raw in compiled[1]:
            effect=deepcopy(dict(raw))
            for field,coefficient in coefficients.items():
                value=effect.get(field)
                if not isinstance(value,dict) or value.get('kind')!=SCALAR_AMOUNT_KIND:continue
                spec=ScalarEffectAmountSpec.from_dict(value)
                effect[field]=replace(spec,coefficient=spec.coefficient*abs(coefficient),binding_id=identity).to_dict()
                modified+=1
            effects.append(effect)
    except (TypeError,ValueError):return None
    if not modified:return None
    return compiled[0],tuple(effects),compiled[2],compiled[3]
