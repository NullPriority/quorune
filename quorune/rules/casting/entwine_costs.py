from __future__ import annotations

"""Correlate ordinary/all-mode selections and Entwine before modifiers."""

from copy import deepcopy
from ...entwine_model import ENTWINE_FIXED_MECHANIC,EntwineModeCost
from ..modal_selection import canonical_modes


def with_entwine_mode_cost_options(program,schema,options,response,*,hint):
    if 'entwine' not in schema:return options
    try:
        price=EntwineModeCost.from_dict(schema['entwine']);price.validate_schema(program.target_schema)
        if ENTWINE_FIXED_MECHANIC not in program.coverage or schema['entwine']!=program.cost_schema.get('entwine'):
            return []
        if response.get('modes'):
            selections=(canonical_modes(program.target_schema,response['modes']),)
        elif hint:selections=price.selections()
        else:return []
        selections=tuple((selected,price.additional_mana(selected)) for selected in selections)
    except (TypeError,ValueError,KeyError,AttributeError):return []
    result=[]
    for option in options:
        for selected,additional in selections:
            choice=deepcopy(option)
            for symbol,amount in additional.items():choice['requirements'][symbol]+=amount
            choice['id']=option['id']+'+entwine:'+','.join(selected)
            choice['entwine_base_option_id']=option['id'];choice['entwine_modes']=list(selected)
            label='all modes (entwine)' if len(selected)>1 else selected[0].replace('_',' ')
            choice['label']=str(option.get('label') or 'Cast')+': '+label
            choice['target_schema']={'mode_count':len(selected),'min_modes':len(selected),'max_modes':len(selected),
                'modes':{mode:deepcopy(program.target_schema['modes'][mode]) for mode in selected}}
            result.append(choice)
    return result
