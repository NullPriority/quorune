from __future__ import annotations

"""Exact declaration shape for fixed resolution mana bundles."""

from ..compiler.fixed_resolution_mana import FIXED_RESOLUTION_MANA_CAPABILITY,FIXED_RESOLUTION_MANA_MECHANIC
from ..mana_restrictions import valid_mana_spend_restriction


def fixed_resolution_mana_capabilities(*,effects,target_schema,mechanic_ids):
    if FIXED_RESOLUTION_MANA_MECHANIC not in mechanic_ids or target_schema is not None or not effects:return ()
    colors=[];total=0;restrictions=set()
    for effect in effects:
        if (set(effect)-{'spend_restriction'}!={'op','player','color','amount','source'} or effect['op']!='mana' or effect['player']!='$controller' or effect['source']!='$source'
                or type(effect['color']) is not str or len(effect['color'])!=1 or effect['color'] not in 'WUBRGC' or type(effect['amount']) is not int or effect['amount']<=0):return ()
        if 'spend_restriction' in effect and not valid_mana_spend_restriction(effect['spend_restriction']):return ()
        restrictions.add(effect.get('spend_restriction'))
        colors.append(effect['color']);total+=effect['amount']
    return (FIXED_RESOLUTION_MANA_CAPABILITY,) if 1<=total<=10 and len(colors)==len(set(colors)) and len(restrictions)==1 else ()


def is_closed_fixed_resolution_mana_program(program):
    required=fixed_resolution_mana_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
