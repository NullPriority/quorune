from __future__ import annotations

"""Closed cost and independently owned result binding for payment v2."""

from typing import Any, Iterable, Mapping, Sequence

from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec
from ..compiler.optional_payment_templates import OPTIONAL_MANA_PAYMENT_OPERATION


def fixed_payment_result_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,
                                     mechanic_ids:Iterable[str])->tuple[str,...]:
    from .fixed_effect_clause_shapes import closed_effect_component_capabilities
    from .closed_effect_program_shapes import closed_effect_program_node_capabilities
    from .fixed_controller_effect_shapes import fixed_controller_effect_sequence_node_capabilities,fixed_counter_controller_effect_sequence_node_capabilities
    mechanics=set(mechanic_ids)-{FIXED_EFFECT_PAYMENT_MECHANIC}
    if len(effects)==1:
        if effects[0].get('op')=='fixed_library_selection':
            from .library_selection_capability_shapes import fixed_library_selection_node_capabilities
            return fixed_library_selection_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
        from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities
        from .node_capability_shapes import fixed_source_characteristics_node_capabilities
        for resolver in (fixed_target_reanimation_node_capabilities,fixed_source_characteristics_node_capabilities):
            result=resolver(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
            if result:return result
        return closed_effect_component_capabilities(effects,target_schema=target_schema,mechanics=mechanics)
    from .fixed_effect_clause_shapes import fixed_effect_clause_sequence_node_capabilities
    for resolver in (fixed_controller_effect_sequence_node_capabilities,fixed_counter_controller_effect_sequence_node_capabilities,
                     fixed_effect_clause_sequence_node_capabilities,closed_effect_program_node_capabilities):
        result=resolver(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
        if result:return result
    return ()


def fixed_effect_payment_node_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,
                                         mechanic_ids:Iterable[str])->tuple[str,...]:
    mechanics=set(mechanic_ids)
    if FIXED_EFFECT_PAYMENT_MECHANIC not in mechanics or len(effects)!=1:return ()
    wrapper=effects[0]
    fields={'op','schema_version','player','payment','effects'}
    if not isinstance(wrapper,Mapping) or set(wrapper) not in (fields,fields|{'cost'}) or wrapper.get('op')!=OPTIONAL_MANA_PAYMENT_OPERATION or type(wrapper.get('schema_version'))is not int or wrapper['schema_version']!=2 or wrapper.get('player')!='$controller':return ()
    try:payment=FixedEffectPaymentSpec.from_dict(wrapper['payment'])
    except (ValueError,TypeError,KeyError):return ()
    if ('cost'in wrapper)!=(payment.kind=='mana') or payment.kind=='mana'and dict(wrapper['cost'])!=dict(payment.requirements):return ()
    if payment.kind=='discard'and payment.predicate.owner!='$controller':return ()
    if payment.kind=='sacrifice'and payment.predicate.controller!='$controller':return ()
    nested=wrapper['effects']
    if not isinstance(nested,(list,tuple))or not 1<=len(nested)<=4 or any(not isinstance(e,Mapping)or e.get('op')in {'offer_optional_effect',OPTIONAL_MANA_PAYMENT_OPERATION}for e in nested):return ()
    dependencies=set(payment.capabilities)
    components=fixed_payment_result_capabilities(effects=nested,target_schema=target_schema,
        mechanic_ids=mechanics-{FIXED_EFFECT_PAYMENT_MECHANIC})
    if not components:return ()
    dependencies.update(components)
    return tuple(sorted(dependencies))
