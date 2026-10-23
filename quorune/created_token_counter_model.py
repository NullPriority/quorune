from __future__ import annotations

"""Closed creation followed by untargeted counters on its actual results."""

from typing import Any,Mapping

CREATED_TOKEN_COUNTER_CAPABILITY='token.creation.post_creation_counter'
CREATED_TOKEN_COUNTER_MECHANIC='created-token-counter-followup'
CREATE_THEN_COUNTER='create_token_then_counters'
COUNTER_CREATED='place_created_token_counters'
DEPENDENCIES=(CREATED_TOKEN_COUNTER_CAPABILITY,'token.creation.fixed_definition',
    'counter.producer.fixed_effect','counter.placement.quantity_replacement')


def validate_created_token_counter(effect:Mapping[str,Any],*,resolved:bool=False):
    if not isinstance(effect,Mapping):raise ValueError('Created-token counter instruction must be an object')
    fields={'op','schema_version','controller','source','amount'}
    fields|={'token','token_mechanics'} if effect.get('op')==CREATE_THEN_COUNTER else {'cards'}
    if (not isinstance(effect,Mapping) or set(effect)!=fields or effect.get('op') not in {CREATE_THEN_COUNTER,COUNTER_CREATED}
            or type(effect.get('schema_version')) is not int or effect['schema_version']!=1
            or any(type(effect[k]) is not str or not effect[k] for k in ('controller','source'))):
        raise ValueError('Created-token counter instruction has a closed schema')
    amount=effect['amount']
    if type(amount) is int:
        if amount<0:raise ValueError('Counter amount must be nonnegative')
    elif not resolved and isinstance(amount,Mapping):
        from .query_effect_amount_model import CastXAmountSpec,PublicQueryAmountSpec,CAST_X_AMOUNT_KIND,PUBLIC_QUERY_AMOUNT_KIND
        from .scalar_effect_amount_model import ScalarEffectAmountSpec,SCALAR_AMOUNT_KIND
        constructors={CAST_X_AMOUNT_KIND:CastXAmountSpec,PUBLIC_QUERY_AMOUNT_KIND:PublicQueryAmountSpec,SCALAR_AMOUNT_KIND:ScalarEffectAmountSpec}
        constructor=constructors.get(amount.get('kind'))
        if constructor is None:raise ValueError('Counter amount producer is not represented')
        spec=constructor.from_dict(amount)
        if spec.coefficient<0:raise ValueError('Counter amount producer must be nonnegative')
    else:raise ValueError('Counter amount must use a closed numeric producer')
    if effect['op']==CREATE_THEN_COUNTER:
        from .rules.token_creation_capability_shapes import fixed_token_creation_node_capabilities
        token=effect['token'];mechanics=effect['token_mechanics']
        if (not isinstance(token,Mapping) or token.get('op')!='create_token' or type(token.get('quantity')) is not int or token['quantity']!=1
                or token.get('controller')!=effect['controller'] or not isinstance(mechanics,(list,tuple))
                or any(type(m) is not str for m in mechanics)):
            raise ValueError('Creation subject must be one complete ordinary token definition')
        from .replacement.immutable import thaw_value
        normalized={**thaw_value(token),'controller':'$controller'}
        if 'token.creation.fixed_definition' not in fixed_token_creation_node_capabilities(effects=(normalized,),target_schema=None,mechanic_ids=set(mechanics)):
            raise ValueError('Creation subject is not a closed fixed definition')
    else:
        refs=effect['cards']
        if not isinstance(refs,(list,tuple)) or any(type(ref) is not str or not ref for ref in refs) or len(set(refs))!=len(refs):
            raise ValueError('Created object results must be distinct references')
    return effect


def created_token_counter_payload_dependencies(value):
    if not isinstance(value,Mapping) or value.get('op') not in {CREATE_THEN_COUNTER,COUNTER_CREATED}:return ()
    validate_created_token_counter(value)
    return DEPENDENCIES
