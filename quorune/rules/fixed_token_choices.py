"""Token choice closure delegates each branch to its existing definition owner."""
from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..compiler.fixed_token_choices import TOKEN_CHOICE_CAPABILITY,TOKEN_CHOICE_MECHANIC,TOKEN_CHOICE_PROMPT
from ..fixed_token_production import fixed_ordinary_token_name


def fixed_token_choice_node_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or len(effects)!=1 or TOKEN_CHOICE_MECHANIC not in mechanic_ids:return ()
    choice = effects[0]
    if (not isinstance(choice,Mapping) or set(choice)!={'op','player','prompt','options','then_by_choice'}
            or choice['op']!='choose_option' or choice['player']!='$controller'
            or choice['prompt']!=TOKEN_CHOICE_PROMPT):return ()
    options,branches = choice['options'],choice['then_by_choice']
    if not isinstance(options,(list,tuple)) or not 2<=len(options)<=4 or not isinstance(branches,Mapping):return ()
    names = []
    for option in options:
        if (not isinstance(option,Mapping) or set(option)!={'id','label'}
                or type(option['id']) is not str or not option['id'].isascii() or not option['id'].isalpha()
                or option['id']!=option['label']):return ()
        names.append(option['id'])
    if len(set(names))!=len(names) or set(branches)!=set(names):return ()
    from .token_creation_capability_shapes import _fixed_token_effect_closure,_token_specific_mechanics
    dependencies,mechanics = {TOKEN_CHOICE_CAPABILITY},{TOKEN_CHOICE_MECHANIC}
    for name in names:
        branch = branches[name]
        if not isinstance(branch,(list,tuple)) or len(branch)!=1 or not isinstance(branch[0],Mapping):return ()
        token = branch[0]
        if (token.get('op')!='create_token' or token.get('controller')!='$controller'
                or type(token.get('quantity')) is not int or token['quantity']!=1
                or token.get('name')!=fixed_ordinary_token_name(name) or 'tapped' in token):return ()
        closure = _fixed_token_effect_closure(token)
        if closure is None:return ()
        owned,represented = closure
        dependencies.update(owned);mechanics.update(represented)
    if _token_specific_mechanics(set(mechanic_ids)-{CURRENT_ABILITY_FRAGMENT_COVERAGE})!=mechanics:return ()
    return tuple(sorted(dependencies))


def fixed_token_choice_payload_dependencies(value,mechanics):
    if not isinstance(value,Mapping) or value.get('op')!='choose_option':return ()
    marked = TOKEN_CHOICE_MECHANIC in mechanics or value.get('prompt')==TOKEN_CHOICE_PROMPT
    if not marked:return ()
    required = fixed_token_choice_node_capabilities(effects=(value,),target_schema=None,mechanic_ids=mechanics)
    if not required:raise ValueError('Fixed token choice lacks complete controller and definition authority')
    return required
