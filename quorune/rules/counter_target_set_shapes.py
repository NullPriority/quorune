from __future__ import annotations

"""Versioned target-set counter payloads retain scalar predicate/amount owners."""

from collections.abc import Mapping
from ..counter_names import EXISTING_COUNTER_AMOUNT,is_existing_counter_amount
from ..target_cardinality import validate_selected_target_count

QUANTIFIED_COUNTER_CAPABILITY='counter.producer.quantified_target_set'
QUANTIFIED_COUNTER_MECHANIC='quantified-counter-target-set'


def validate_quantified_counter_target_effect(effect,*,resolved=False):
    required={'op','schema_version','cards','maximum_targets','counter','amount','source'}
    optional={'announced_target_count'}|({'reason','_replacement_selections'} if resolved else set())
    if not isinstance(effect,Mapping) or not required<=set(effect) or set(effect)-required-optional:
        raise ValueError('Quantified counter target fields are malformed')
    if effect['op']!='place_counters_on_targets' or type(effect['schema_version']) is not int or effect['schema_version']!=2:
        raise ValueError('Quantified counter target version is unsupported')
    variable=effect.get('announced_target_count') is True
    if 'announced_target_count' in effect and not variable:raise ValueError('Counter cardinality marker must be true')
    maximum=effect['maximum_targets']
    if variable and not resolved:validate_selected_target_count(maximum)
    elif type(maximum) is not int or not (maximum>=0 if variable else 1<=maximum<=6):
        raise ValueError('Quantified counter target maximum is invalid')
    name=effect['counter'];amount=effect['amount']
    if type(name) is not str or not name or name!=' '.join(name.casefold().split()):raise ValueError('Counter name is not canonical')
    relative=is_existing_counter_amount(amount) or (resolved and type(amount) is str and amount==EXISTING_COUNTER_AMOUNT)
    if not relative and (type(amount) is not int or amount<=0):raise ValueError('Counter amount must be fixed positive or existing named count')
    if not resolved and (effect['cards']!='$targets' or effect['source']!='$source'):raise ValueError('Counter targets require their selected set and source')
    return variable,relative


def quantified_counter_target_capabilities(*,effects,target_schema,mechanic_ids):
    if QUANTIFIED_COUNTER_MECHANIC not in mechanic_ids or len(effects)!=1 or not isinstance(target_schema,Mapping):return ()
    effect=effects[0]
    try:
        variable,relative=validate_quantified_counter_target_effect(effect)
        if variable:return ()
        from .fixed_homogeneous_target_set_capability_shapes import _singular_schema,_target_bounds
        normalized=_singular_schema(target_schema);bounds=_target_bounds(target_schema)
        if normalized is None or bounds is None or normalized[1] or bounds[1]!=effect['maximum_targets']:return ()
        scalar={k:v for k,v in effect.items() if k not in {'schema_version','cards','maximum_targets'}}
        scalar.update(op='place_counters',card='$target.0')
        from .counter_placement_capability_shapes import fixed_counter_placement_node_capabilities
        owned=fixed_counter_placement_node_capabilities(effects=(scalar,),target_schema=normalized[0],mechanic_ids=mechanic_ids)
        if not owned:return ()
        from .capabilities import capability_dependencies_for_node
        ambient=capability_dependencies_for_node(effects=(scalar,),target_schema=normalized[0],
            mechanic_ids=set(mechanic_ids)-{QUANTIFIED_COUNTER_MECHANIC,'any-number-target-set'})
        return tuple(sorted({*owned,*ambient,QUANTIFIED_COUNTER_CAPABILITY,'counter.producer.fixed_permanent_target_set_effect','target.revalidate_resolution'}))
    except (ValueError,TypeError,KeyError):return ()


def quantified_counter_payload_dependencies(value):
    if value.get('op')!='place_counters_on_targets' or 'schema_version' not in value:return ()
    _variable,relative=validate_quantified_counter_target_effect(value)
    return (QUANTIFIED_COUNTER_CAPABILITY,'counter.producer.fixed_permanent_target_set_effect',*(('counter.producer.named_doubling',) if relative else ()))


def is_closed_quantified_counter_target_program(program):
    if QUANTIFIED_COUNTER_MECHANIC not in program.coverage:return False
    from .any_number_target_shapes import any_number_target_node_capabilities
    required=(any_number_target_node_capabilities if 'any-number-target-set' in program.coverage else quantified_counter_target_capabilities)(
        effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
