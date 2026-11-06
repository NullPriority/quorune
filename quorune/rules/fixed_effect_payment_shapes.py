from __future__ import annotations

"""Closed cost and independently owned result binding for payment v2."""

from typing import Any, Iterable, Mapping, Sequence

from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC, FixedEffectPaymentSpec
from ..compiler.optional_payment_templates import OPTIONAL_MANA_PAYMENT_OPERATION
from .blight_model import blight_decline_branch_is_closed


def fixed_payment_result_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,
                                     mechanic_ids:Iterable[str])->tuple[str,...]:
    from .fixed_effect_clause_shapes import closed_effect_component_capabilities
    from .closed_effect_program_shapes import closed_effect_program_node_capabilities
    from .fixed_controller_effect_shapes import fixed_controller_effect_sequence_node_capabilities,fixed_counter_controller_effect_sequence_node_capabilities
    mechanics=set(mechanic_ids)-{FIXED_EFFECT_PAYMENT_MECHANIC}
    if len(effects)==1:
        from .affected_player_discard_capability_shapes import fixed_context_player_discard_node_capabilities
        from .event_player_result_shapes import fixed_event_player_result_node_capabilities
        from .event_player_choice_shapes import event_player_sacrifice_node_capabilities
        from .mill_capability_shapes import fixed_mill_node_capabilities
        for resolver in (fixed_context_player_discard_node_capabilities,fixed_event_player_result_node_capabilities,
                         event_player_sacrifice_node_capabilities,fixed_mill_node_capabilities):
            result=resolver(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
            if result:return result
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
    from .target_player_sequences import target_player_sequence_node_capabilities
    from .target_set_result_sequences import target_set_result_sequence_capabilities
    for resolver in (fixed_controller_effect_sequence_node_capabilities,fixed_counter_controller_effect_sequence_node_capabilities,
                     fixed_effect_clause_sequence_node_capabilities,target_player_sequence_node_capabilities,
                     target_set_result_sequence_capabilities,closed_effect_program_node_capabilities):
        result=resolver(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
        if result:return result
    return ()


def fixed_payment_event_player_runtime_dependencies(program):
    """Keep occurrence-player proof when a closed result sits behind a cost."""
    players={'$context.player','$context.target','$context.defending_player','$context.previous_controller','$context.controller'}
    def references(value):
        if isinstance(value,Mapping):return set().union(*(references(child) for child in value.values()))
        if isinstance(value,(tuple,list)):return set().union(*(references(child) for child in value))
        return {value} if isinstance(value,str) and value in players else set()
    required=set()
    from .event_player_result_shapes import _event_player_reference_is_closed
    def visit(value):
        if isinstance(value,Mapping):
            if value.get('op')==OPTIONAL_MANA_PAYMENT_OPERATION and value.get('schema_version')==2:
                payment=value.get('payment')
                if not isinstance(payment,Mapping):raise ValueError('Fixed payment descriptor is unavailable')
                refs=references(value.get('effects',())) | references(value.get('declined_effects',()))
                if refs or payment.get('kind')=='source_sacrifice':
                    owned=fixed_effect_payment_node_capabilities(effects=(value,),target_schema=program.target_schema,mechanic_ids=program.coverage)
                    if not owned or any(not _event_player_reference_is_closed(program,ref) for ref in refs):
                        raise ValueError('Fixed payment result lacks its closed occurrence-player scope')
                    required.update(owned)
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))


def fixed_effect_payment_node_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,
                                         mechanic_ids:Iterable[str])->tuple[str,...]:
    mechanics=set(mechanic_ids)
    if FIXED_EFFECT_PAYMENT_MECHANIC not in mechanics or len(effects)!=1:return ()
    wrapper=effects[0]
    if isinstance(wrapper, Mapping) and wrapper.get('schema_version') == 4:
        from .reflexive_payment_shapes import reflexive_payment_node_capabilities
        return reflexive_payment_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if isinstance(wrapper, Mapping) and wrapper.get('schema_version') == 3:
        from ..source_maintenance import SOURCE_MAINTENANCE_MECHANIC, SourceMaintenanceSpec
        if target_schema is not None or SOURCE_MAINTENANCE_MECHANIC not in mechanics:
            return ()
        try:
            spec = SourceMaintenanceSpec.from_effect(wrapper)
        except (KeyError, TypeError, ValueError):
            return ()
        if wrapper['player'] != '$controller' or wrapper['source'] != '$source.zone_object':
            return ()
        payment = spec.payment
        if payment is not None and (
            payment.kind in {'discard','exile','mana_discard'} and payment.predicate.owner != '$controller'
            or payment.kind in {'sacrifice','tap'} and payment.predicate.controller != '$controller'
            or payment.kind in {'remove_source_counter','source_sacrifice'} and payment.source_ref!='$source.zone_object'
        ):
            return ()
        return spec.capabilities
    fields={'op','schema_version','player','payment','effects'}
    if isinstance(wrapper, Mapping) and "declined_effects" in wrapper:
        fields.add("declined_effects")
    if not isinstance(wrapper,Mapping) or set(wrapper) not in (fields,fields|{'cost'}) or wrapper.get('op')!=OPTIONAL_MANA_PAYMENT_OPERATION or type(wrapper.get('schema_version'))is not int or wrapper['schema_version']!=2 or wrapper.get('player')!='$controller':return ()
    try:payment=FixedEffectPaymentSpec.from_dict(wrapper['payment'], allow_mandatory=True)
    except (ValueError,TypeError,KeyError):return ()
    if ('cost'in wrapper)!=(payment.kind=='mana') or payment.kind=='mana'and dict(wrapper['cost'])!=dict(payment.requirements):return ()
    if payment.kind in {'discard','exile','mana_discard'}and payment.predicate.owner!='$controller':return ()
    if payment.kind in {'sacrifice','tap','blight'}and payment.predicate.controller!='$controller':return ()
    if payment.kind=='sacrifice' and payment.schema_version==6 and payment.predicate.exclude_ref!='$source.zone_object':return ()
    if payment.kind in {'remove_source_counter','source_sacrifice'} and payment.source_ref!='$source.zone_object':return ()
    nested=wrapper['effects']
    if "declined_effects" in wrapper:
        if target_schema is not None or not blight_decline_branch_is_closed(payment.kind, nested, wrapper["declined_effects"]):
            return ()
        nested = wrapper["declined_effects"]
    if not isinstance(nested,(list,tuple))or not 1<=len(nested)<=4 or any(not isinstance(e,Mapping)or e.get('op')in {'offer_optional_effect',OPTIONAL_MANA_PAYMENT_OPERATION}for e in nested):return ()
    dependencies=set(payment.capabilities)
    components=fixed_payment_result_capabilities(effects=nested,target_schema=target_schema,
        mechanic_ids=mechanics-{FIXED_EFFECT_PAYMENT_MECHANIC})
    if not components:return ()
    dependencies.update(components)
    return tuple(sorted(dependencies))
