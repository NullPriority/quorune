from __future__ import annotations

"""Fixed characteristic results over one homogeneous revalidated target set."""

from typing import Any,Mapping,Sequence
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES

TARGET_CHARACTERISTIC_SET_CAPABILITY='continuous.resolution.fixed_target_characteristic_set'
OPERATION='apply_source_characteristics_until_end_of_turn'


def decode_target_characteristics(effect):
    if not isinstance(effect,Mapping) or set(effect)!={'op','schema_version','cards','maximum_targets','power','toughness','keywords'}:
        raise ValueError('Target characteristic set fields are malformed')
    if effect['op']!=OPERATION or type(effect['schema_version']) is not int or effect['schema_version']!=6:
        raise ValueError('Target characteristic set version is unsupported')
    if type(effect['maximum_targets']) is not int or not 1<=effect['maximum_targets']<=6:
        raise ValueError('Target characteristic set bound is invalid')
    if type(effect['power']) is not int or type(effect['toughness']) is not int:
        raise ValueError('Target characteristic set modifier is fixed integral')
    keywords=effect['keywords']
    if not isinstance(keywords,(list,tuple)) or len(set(keywords))!=len(keywords) or any(k not in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES for k in keywords):
        raise ValueError('Target characteristic keywords are unsupported')
    if not (effect['power'] or effect['toughness'] or keywords):raise ValueError('Target characteristic set is empty')
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
    if not {k.casefold() for k in keywords}<=mechanics:return ()
    return tuple(sorted({TARGET_CHARACTERISTIC_SET_CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn','target.revalidate_resolution',
        *direct_target_predicate_capabilities(singular),*(cap for keyword in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])}))


def is_closed_target_characteristic_set_program(program):
    required=target_characteristic_set_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
