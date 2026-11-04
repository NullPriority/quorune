from __future__ import annotations

"""Resolved-count bridge to the existing fixed library partition owner."""

from collections.abc import Mapping
from copy import deepcopy

VARIABLE_LIBRARY_SELECTION_OPERATION = 'variable_library_selection'
VARIABLE_LIBRARY_SELECTION_MECHANIC = 'variable-library-selection'
VARIABLE_LIBRARY_SELECTION_CAPABILITY = 'library.select.variable_controller'
ALL_LOOKED = 'all_looked'
_FIELDS = {'op','schema_version','player','look_count','public_reveal','selected_reveal',
    'selection_policy','minimum','maximum','predicate_groups','remainder_destination','remainder_order'}


def _amount(value,*,resolved):
    if type(value) is int:
        if value < 0:raise ValueError('Library inspection count must be nonnegative')
        return
    if resolved or not isinstance(value,Mapping):
        raise ValueError('Library inspection count must be resolved before looking')
    from .query_effect_amount_model import (PublicQueryAmountSpec,CastXAmountSpec,
        PUBLIC_QUERY_AMOUNT_KIND,CAST_X_AMOUNT_KIND)
    from .scalar_effect_amount_model import ScalarEffectAmountSpec,SCALAR_AMOUNT_KIND
    constructor={PUBLIC_QUERY_AMOUNT_KIND:PublicQueryAmountSpec,CAST_X_AMOUNT_KIND:CastXAmountSpec,
        SCALAR_AMOUNT_KIND:ScalarEffectAmountSpec}.get(value.get('kind'))
    if constructor is None or constructor.from_dict(value).coefficient < 0:
        raise ValueError('Library inspection amount producer is unrepresented')


def validate_variable_library_selection(effect,*,resolved=False):
    if (not isinstance(effect,Mapping) or set(effect)!=_FIELDS
            or effect.get('op')!=VARIABLE_LIBRARY_SELECTION_OPERATION
            or type(effect['schema_version']) is not int or effect['schema_version']!=1
            or type(effect['player']) is not str or not effect['player'] or effect['player'].strip()!=effect['player']):
        raise ValueError('Variable library selection has a closed schema')
    _amount(effect['look_count'],resolved=resolved)
    minimum,maximum=effect['minimum'],effect['maximum']
    dynamic=maximum==ALL_LOOKED
    if (type(minimum) is not int or minimum<0
            or not dynamic and (type(maximum) is not int or maximum<=0 or minimum>maximum)
            or dynamic and (minimum!=0 or effect['selection_policy'] not in {'up_to_matching','all_matching'})):
        raise ValueError('Variable library selection cardinality is malformed')
    groups=effect['predicate_groups']
    if effect['selection_policy']=='optional_slots' and (not isinstance(groups,(list,tuple)) or len(groups)!=2):
        raise ValueError('Variable optional selection represents two independently typed slots')
    proxy_count=max(1,minimum,maximum if not dynamic else 1)
    proxy={key:deepcopy(value) for key,value in effect.items() if key!='schema_version'}
    proxy.update(op='fixed_library_selection',player='$controller',look_count=proxy_count,
        maximum=proxy_count if dynamic else maximum)
    from .rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities
    if not fixed_library_selection_node_capabilities(effects=(proxy,),target_schema=None,
            mechanic_ids={'fixed-library-selection'}):
        raise ValueError('Variable selection does not have a complete existing fixed selection owner')


def fixed_selection_for_resolved_count(effect):
    """CR609.3: normalize before inspection; zero never reaches a look owner."""
    validate_variable_library_selection(effect,resolved=True)
    count=effect['look_count']
    if count==0:return None
    result={key:deepcopy(value) for key,value in effect.items() if key!='schema_version'}
    result['op']='fixed_library_selection'
    result['minimum']=min(result['minimum'],count)
    result['maximum']=count if result['maximum']==ALL_LOOKED else min(result['maximum'],count)
    if result['selection_policy']=='optional_slots' and count==1:
        alternatives=[]
        for group in result['predicate_groups']:
            for predicate in group:
                if predicate not in alternatives:alternatives.append(predicate)
        result.update(selection_policy='up_to_matching',predicate_groups=[alternatives],minimum=0,maximum=1)
    return result


def variable_library_selection_payload_dependencies(value):
    if not isinstance(value,Mapping) or value.get('op')!=VARIABLE_LIBRARY_SELECTION_OPERATION:return ()
    validate_variable_library_selection(value)
    return VARIABLE_LIBRARY_SELECTION_CAPABILITY,'library.select.fixed_controller'
