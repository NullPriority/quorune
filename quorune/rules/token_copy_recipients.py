"""One target creates a copy of a separately targeted controlled creature."""
from ..replacement.immutable import FrozenMap
from ..token_copy_recipient_model import (COPY_RECIPIENT_FIELD,COPY_RECIPIENT_CAPABILITY,
    COPY_RECIPIENT_MECHANIC,TokenCopyRecipientBinding)
from ..token_copy_recipes import TokenCopyRecipeSpec

COPY_SOURCE_SCHEMA={'zones':['battlefield'],'categories':['permanent'],'count':1,
    'types_any':['creature'],'controller_relation':'you'}
COPY_RECIPIENT_SCHEMA={'zones':['player'],'categories':['player'],'player_relation':'any','count':1}
COPY_TARGET_SCHEMA={'groups':[{'id':'recipient',**COPY_RECIPIENT_SCHEMA},{'id':'copy_source',**COPY_SOURCE_SCHEMA}]}
_COPY_MECHANICS={'cr-111-tokens','fixed-token-production','fixed-token-copy','cr-707-copying-objects',
    'cr-115-targets',COPY_RECIPIENT_MECHANIC}


def _binding(effect):
    binding=TokenCopyRecipientBinding.from_dict(effect.get(COPY_RECIPIENT_FIELD))
    if (effect.get('op')!='create_token' or type(effect.get('quantity')) is not int or effect['quantity']!=1
            or TokenCopyRecipeSpec.from_dict(effect.get('copy_spec'))!=TokenCopyRecipeSpec(origin='target')):
        raise ValueError('Target-player copying requires its complete ordinary creature recipe')
    return binding


def target_copy_recipient_node_dependencies(effect,target_schema,mechanics):
    try:
        _binding(effect)
        if (set(effect)!={'op','controller','quantity','copy_spec',COPY_RECIPIENT_FIELD}
                or effect['controller']!='$target.0' or FrozenMap(target_schema or {})!=FrozenMap(COPY_TARGET_SCHEMA)
                or COPY_RECIPIENT_MECHANIC not in mechanics):return ()
        normalized={k:v for k,v in effect.items() if k!=COPY_RECIPIENT_FIELD}
        normalized['controller']='$controller'
        from .token_creation_capability_shapes import _copy_recipe_capabilities
        old=_copy_recipe_capabilities(normalized,COPY_SOURCE_SCHEMA,_COPY_MECHANICS)
        return tuple(sorted({COPY_RECIPIENT_CAPABILITY,*old})) if old else ()
    except (ValueError,TypeError,KeyError):return ()


def target_copy_recipient_runtime_dependencies(program):
    from ..token_copy_recipes import executable_copy_effects
    required=set()
    for effect in executable_copy_effects(program.effects):
        if COPY_RECIPIENT_FIELD not in effect:continue
        owned=target_copy_recipient_node_dependencies(effect,program.target_schema,_COPY_MECHANICS)
        if not owned:raise ValueError('Target-player copy lost its exact bound target roles')
        required.update(owned)
    return tuple(sorted(required))


def resolved_copy_recipient(effect,item):
    binding=_binding(effect)
    if len(item.targets)!=2:raise ValueError('Target-player copy lost an original announcement slot')
    recipient=item.targets[binding.recipient_index]
    if recipient is None or item.targets[binding.copy_index] is None:return None
    if type(recipient) is not str or effect.get('controller')!=recipient:
        raise ValueError('Resolved token recipient differs from its original target')
    return recipient
