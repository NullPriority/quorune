"""Activation-time use of the shared immutable public-state condition owner."""

from functools import partial

from ...activation_condition_model import ActivationConditionKind,ACTIVATION_PUBLIC_STATE_CAPABILITY
from ...card_programs.runtime import fixed_public_state_condition_matches
from ...dynamic_characteristics import query_characteristic_count


def activation_public_state_dependencies(conditions):
    from ...object_predicate import object_query_capabilities
    from ...characteristic_fragments import characteristic_quantity_capabilities
    from ...public_history_query_model import public_history_query_dependencies
    from ...public_zone_condition_model import public_zone_condition_capabilities
    required=set()
    for condition in conditions:
        if condition.kind is not ActivationConditionKind.FIXED_PUBLIC_STATE:continue
        raw=condition.public_state.to_dict()
        required.update((ACTIVATION_PUBLIC_STATE_CAPABILITY,*object_query_capabilities(raw),*characteristic_quantity_capabilities(raw),
            *public_history_query_dependencies(raw),*public_zone_condition_capabilities(raw)))
    return tuple(sorted(required))


def activation_public_state_status(host,seat,source,condition):
    if source is None:return 'unresolved','activation_public_source_required'
    if source.controller!=seat:return 'unavailable','activation_public_source_not_controlled'
    try:
        result=fixed_public_state_condition_matches(host.state,source,condition.public_state,
            public_object_resolver=partial(host._public_object_query_result,_maximum_layer=None),
            quantity_resolver=partial(query_characteristic_count,host,_maximum_layer=None),require_available=True)
    except (AttributeError,KeyError,TypeError,ValueError):return 'unresolved','unavailable_activation_public_state'
    return ('payable',None) if result else ('unavailable','requires_activation_public_state')
