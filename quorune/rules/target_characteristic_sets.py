from __future__ import annotations

"""Fixed characteristic results over one homogeneous revalidated target set."""

from typing import Any,Mapping,Sequence
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..keyword_counters import keyword_counter_mechanic

TARGET_CHARACTERISTIC_SET_CAPABILITY='continuous.resolution.fixed_target_characteristic_set'
OPERATION='apply_source_characteristics_until_end_of_turn'


def decode_target_characteristics(effect,*,allow_zero_result=False):
    variable = isinstance(effect,Mapping) and effect.get('announced_target_count') is True
    if not isinstance(effect,Mapping) or set(effect)!={'op','schema_version','cards','maximum_targets','power','toughness','keywords'} | ({'announced_target_count'} if variable else set()):
        raise ValueError('Target characteristic set fields are malformed')
    if effect['op']!=OPERATION or type(effect['schema_version']) is not int or effect['schema_version']!=6:
        raise ValueError('Target characteristic set version is unsupported')
    if type(effect['maximum_targets']) is not int or not (effect['maximum_targets']>=0 if variable else 1<=effect['maximum_targets']<=6):
        raise ValueError('Target characteristic set bound is invalid')
    if type(effect['power']) is not int or type(effect['toughness']) is not int:
        raise ValueError('Target characteristic set modifier is fixed integral')
    keywords=effect['keywords']
    if (not isinstance(keywords,(list,tuple)) or any(type(k) is not str or k not in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES for k in keywords)
            or len(set(keywords))!=len(keywords)):
        raise ValueError('Target characteristic keywords are unsupported')
    if not allow_zero_result and not (effect['power'] or effect['toughness'] or keywords):raise ValueError('Target characteristic set is empty')
    return tuple(keywords)


def target_characteristic_set_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,mechanic_ids):
    mechanics=set(mechanic_ids)
    if not {'cr-115-targets','cr-611-continuous-effects'}<=mechanics or len(effects)!=1:return ()
    effect=effects[0]
    try:keywords=decode_target_characteristics(effect)
    except (ValueError,TypeError,KeyError):return ()
    if effect['cards']!='$targets' or not isinstance(target_schema,Mapping):return ()
    from .fixed_homogeneous_target_set_capability_shapes import _singular_schema,_target_bounds
    from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed,direct_target_predicate_capabilities
    parsed=_singular_schema(target_schema);bounds=_target_bounds(target_schema)
    if parsed is None or bounds is None or parsed[1] or bounds[1]!=effect['maximum_targets']:return ()
    singular=parsed[0]
    if not direct_permanent_target_schema_is_closed(singular) or singular.get('types_any')!=['creature']:return ()
    if not {keyword_counter_mechanic(k) or k.casefold() for k in keywords}<=mechanics:return ()
    return tuple(sorted({TARGET_CHARACTERISTIC_SET_CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn','target.revalidate_resolution',
        *direct_target_predicate_capabilities(singular),*(cap for keyword in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])}))


def is_closed_target_characteristic_set_program(program):
    required=target_characteristic_set_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)


def target_characteristic_set_runtime_dependencies(program):
    """Payload-owned minimums also apply to composed and modal instructions."""
    dependencies=set()
    def visit(value):
        if isinstance(value,Mapping):
            if value.get('op')==OPERATION and value.get('schema_version')==6:
                fixed=value
                if 'announced_target_count' in value:
                    from .any_number_target_shapes import any_number_target_node_capabilities
                    required=any_number_target_node_capabilities(effects=(value,),target_schema=program.target_schema,mechanic_ids=program.coverage)
                    if not required:raise ValueError('Any-number characteristic result lacks its cardinality owner')
                    dependencies.update(required)
                    fixed={key:child for key,child in value.items() if key!='announced_target_count'}
                    fixed['maximum_targets']=2
                if any(isinstance(value.get(field),Mapping) for field in ('power','toughness')):
                    from ..compiler.public_query_effect_amounts import public_query_amount_shape_context
                    context=public_query_amount_shape_context((value,),set(program.coverage),target_schema=program.target_schema)
                    if context is None:raise ValueError('Target characteristic amount lacks its existing quantity owner')
                    fixed=context[0][0]
                keywords=decode_target_characteristics(fixed)
                dependencies.update((TARGET_CHARACTERISTIC_SET_CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn','target.revalidate_resolution'))
                dependencies.update(cap for keyword in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])
            for child in value.values():visit(child)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child)
    visit((program.effects,program.target_schema))
    return tuple(sorted(dependencies))
