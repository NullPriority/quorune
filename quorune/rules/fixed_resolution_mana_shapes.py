from __future__ import annotations

"""Exact declaration shape for fixed resolution mana bundles."""

from ..compiler.fixed_resolution_mana import FIXED_RESOLUTION_MANA_CAPABILITY,FIXED_RESOLUTION_MANA_MECHANIC
from ..mana_restrictions import valid_mana_spend_restriction


def fixed_resolution_mana_capabilities(*,effects,target_schema,mechanic_ids):
    if FIXED_RESOLUTION_MANA_MECHANIC not in mechanic_ids or target_schema is not None or not effects:return ()
    if len(effects)==1 and effects[0].get('op')=='choose_mana':
        from ..resolution_mana_model import ResolutionManaChoiceSpec,RESOLUTION_MANA_CHOICE_CAPABILITY
        try:ResolutionManaChoiceSpec.from_effect(effects[0])
        except (TypeError,ValueError,KeyError):return ()
        if effects[0]['player']!='$controller' or effects[0]['source']!='$source':return ()
        return (FIXED_RESOLUTION_MANA_CAPABILITY,RESOLUTION_MANA_CHOICE_CAPABILITY)
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


def fixed_resolution_mana_choice_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or effects[0].get('op')!='choose_mana':return ()
    return fixed_resolution_mana_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)


def fixed_resolution_mana_runtime_dependencies(program):
    """Bind canonical fixed mana leaves even inside unannotated wrappers."""
    from collections.abc import Mapping
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            if (value.get('op')=='mana' and value.get('player')=='$controller'
                    and value.get('source')=='$source' and not isinstance(value.get('amount'),Mapping)):
                if 'retain_until' in value:
                    from ..compiler.keyword_event_effect_nodes import FixedKeywordEventEffectSpec
                    spec=FixedKeywordEventEffectSpec('firebending',value.get('amount'))
                    if value!=spec.effects[0]:raise ValueError('Retained combat mana leaf is malformed')
                    owned=(spec.capability_id,)
                else:
                    owned=fixed_resolution_mana_capabilities(effects=(value,),target_schema=None,mechanic_ids=('fixed-resolution-mana',))
                if not owned:raise ValueError('Fixed resolution mana leaf is malformed')
                required.update(owned)
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit((program.effects,program.target_schema))
    return tuple(sorted(required))
