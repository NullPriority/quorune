from __future__ import annotations

from collections.abc import Mapping
from ..target_cardinality import validate_selected_target_count

ALL_KIND_COUNTER_CAPABILITY='counter.producer.all_kind_doubling'
ALL_KIND_COUNTER_MECHANIC='all-kind-counter-doubling'


def validate_all_kind_counter_effect(effect,*,resolved=False):
    fields={'op','schema_version','cards','maximum_targets','source'}
    allowed=fields|{'announced_target_count'}|({'reason','_replacement_selections'} if resolved else set())
    if not isinstance(effect,Mapping) or not fields<=set(effect) or set(effect)-allowed:raise ValueError('All-kind counter fields are malformed')
    if effect['op']!='double_counters_on_targets' or type(effect['schema_version']) is not int or effect['schema_version']!=1:raise ValueError('All-kind counter version is unsupported')
    variable=effect.get('announced_target_count') is True
    if 'announced_target_count' in effect and not variable:raise ValueError('All-kind counter cardinality marker is invalid')
    maximum=effect['maximum_targets']
    if variable and not resolved:validate_selected_target_count(maximum)
    elif type(maximum) is not int or not (maximum>=0 if variable else 1<=maximum<=6):raise ValueError('All-kind counter maximum is invalid')
    if not resolved and (effect['cards']!='$targets' or effect['source']!='$source'):raise ValueError('All-kind counters require their target set and source')
    return variable


def all_kind_counter_node_capabilities(*,effects,target_schema,mechanic_ids):
    if ALL_KIND_COUNTER_MECHANIC not in mechanic_ids or len(effects)!=1 or not isinstance(target_schema,Mapping):return ()
    try:
        if validate_all_kind_counter_effect(effects[0]):return ()
        from .fixed_homogeneous_target_set_capability_shapes import _singular_schema,_target_bounds
        if target_schema.get('count')==1 and type(target_schema['count']) is int:
            singular=dict(target_schema);maximum=1
        else:
            normalized=_singular_schema(target_schema);bounds=_target_bounds(target_schema)
            if normalized is None or normalized[1] or bounds is None:return ()
            singular=normalized[0];maximum=bounds[1]
        if effects[0]['maximum_targets']!=maximum:return ()
        from ..compiler.direct_target import DirectPermanentTargetSpec
        DirectPermanentTargetSpec.from_target_schema(singular)
        from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities
        from .capabilities import MECHANIC_CAPABILITY_DEPENDENCIES
        ambient={cap for mechanic in mechanic_ids if mechanic.startswith('trigger-event-') for cap in MECHANIC_CAPABILITY_DEPENDENCIES.get(mechanic,())}
        if 'cr-603-handling-triggered-abilities' in mechanic_ids:ambient.add('trigger.placement.apnap')
        return tuple(sorted({ALL_KIND_COUNTER_CAPABILITY,'target.revalidate_resolution',*direct_target_predicate_capabilities(singular),*ambient}))
    except (ValueError,TypeError,KeyError):return ()


def all_kind_counter_payload_dependencies(value):
    if value.get('op')!='double_counters_on_targets':return ()
    validate_all_kind_counter_effect(value)
    return (ALL_KIND_COUNTER_CAPABILITY,)


def is_closed_all_kind_counter_program(program):
    if ALL_KIND_COUNTER_MECHANIC not in program.coverage:return False
    from .any_number_target_shapes import any_number_target_node_capabilities
    owner=any_number_target_node_capabilities if 'any-number-target-set' in program.coverage else all_kind_counter_node_capabilities
    required=owner(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)


def all_kind_counter_component_capabilities(*,effects,target_schema,mechanic_ids):
    if ALL_KIND_COUNTER_MECHANIC not in mechanic_ids:return ()
    from .any_number_target_shapes import any_number_target_node_capabilities
    owner=any_number_target_node_capabilities if 'any-number-target-set' in mechanic_ids else all_kind_counter_node_capabilities
    return owner(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
