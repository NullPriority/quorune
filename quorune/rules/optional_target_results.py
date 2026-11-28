"""Validate and guard the optional target before querying dependent information."""
from collections.abc import Mapping
from ..optional_target_result_model import (
    OPTIONAL_LAND_CONTROLLER_SEARCH_CAPABILITY,
    OPTIONAL_TARGET_RESULT_FIELD,OPTIONAL_TARGET_RESULT_CAPABILITY,OPTIONAL_TARGET_RESULT_MECHANIC,
    OptionalTargetResultSpec,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..target_controller_reference import (
    TARGET_CONTROLLER_INFORMATION_CAPABILITY, controller_reference_index, target_controller_reference,
)
from ..util import stable_json
from ..scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin


def _nonbasic_land_controller_search_dependencies(effect):
    recipient = target_controller_reference()
    search = {
        "op": "search", "searching_player": recipient, "zone": "library",
        "selector": {"supertypes": ["basic"], "types": ["land"]},
        "count": {"minimum": 1, "maximum": 1}, "destination": "battlefield",
        "enters_tapped_override": True, "shuffle_after": True,
    }
    expected = {"op": "offer_optional_effect", "player": recipient, "effects": [search]}
    wrapper = {key: value for key, value in effect.items() if key != OPTIONAL_TARGET_RESULT_FIELD}
    if stable_json(thaw_value(FrozenMap(wrapper))) != stable_json(expected):
        raise ValueError("Optional land search requires one basic land and the original target's controller")
    return (OPTIONAL_LAND_CONTROLLER_SEARCH_CAPABILITY, TARGET_CONTROLLER_INFORMATION_CAPABILITY,
            "effect.choice.optional_fixed", "library.search.fixed_to_battlefield")


def optional_target_result_payload_dependencies(effect):
    if OPTIONAL_TARGET_RESULT_FIELD not in effect:return ()
    OptionalTargetResultSpec.from_dict(effect[OPTIONAL_TARGET_RESULT_FIELD])
    if effect.get("op") == "offer_optional_effect":
        return _nonbasic_land_controller_search_dependencies(effect)
    if (set(effect)!={'op','player','delta',OPTIONAL_TARGET_RESULT_FIELD} or effect['op']!='life'
            or controller_reference_index(effect['player'])!=0):
        raise ValueError('Optional result requires the original target controller life instruction')
    amount=ScalarEffectAmountSpec.from_dict(effect['delta'])
    if (amount.schema_version!=1 or amount.origin is not ScalarAmountOrigin.TARGET
            or amount.characteristic!='power' or amount.coefficient!=1 or amount.binding_id is not None):
        raise ValueError('Optional result requires the original target power')
    return (OPTIONAL_TARGET_RESULT_CAPABILITY,'resolution.target_controller_information',
        'quantity_expression.scalar_effect_amount')


def bind_optional_target_result(effect, targets):
    """Return None before any lookup for an unannounced optional target."""
    if OPTIONAL_TARGET_RESULT_FIELD not in effect:return effect
    optional_target_result_payload_dependencies(effect)
    spec=OptionalTargetResultSpec.from_dict(effect[OPTIONAL_TARGET_RESULT_FIELD])
    if spec.target_is_absent(targets):return None
    return {k:v for k,v in effect.items() if k!=OPTIONAL_TARGET_RESULT_FIELD}


def optional_target_result_node_capabilities(*,effects,target_schema,mechanic_ids):
    if (len(effects)!=2 or OPTIONAL_TARGET_RESULT_MECHANIC not in mechanic_ids
            or not isinstance(target_schema,Mapping)
            or any(not isinstance(effect, Mapping) for effect in effects)):return ()
    prefix,result=effects
    if result.get("op") == "offer_optional_effect":
        return _optional_nonbasic_land_search_node_capabilities(prefix, result, target_schema, mechanic_ids)
    if (prefix!={'op':'exile_permanent_targets','cards':'$targets','maximum_targets':1}
            or type(prefix.get('maximum_targets')) is not int):return ()
    required={'zones':['battlefield'],'categories':['permanent'],'types_any':['creature'],'up_to':1}
    schema=dict(target_schema)
    if 'source_exclusion' in schema:
        if schema.pop('source_exclusion') is not True:return ()
    if schema!=required or type(schema['up_to']) is not int:return ()
    try:deps=optional_target_result_payload_dependencies(result)
    except (TypeError,ValueError):return ()
    if not deps:return ()
    from .capabilities import capability_dependencies_for_node
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities
    from ..compiler.fixed_homogeneous_target_sets import FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC
    mandatory={k:v for k,v in target_schema.items() if k!='up_to'};mandatory['count']=1
    mechanics=tuple(m for m in mechanic_ids if m not in {OPTIONAL_TARGET_RESULT_MECHANIC,FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC})
    old=capability_dependencies_for_node(effects=({'op':'exile_permanent','card':'$target.0'},
        {k:v for k,v in result.items() if k!=OPTIONAL_TARGET_RESULT_FIELD}),target_schema=mandatory,mechanic_ids=mechanics)
    batch=fixed_homogeneous_target_set_node_capabilities(effects=(prefix,),target_schema=target_schema,mechanic_ids=mechanic_ids)
    return tuple(sorted({*deps,*old,*batch})) if old and batch else ()


def _optional_nonbasic_land_search_node_capabilities(prefix, result, target_schema, mechanic_ids):
    from .fixed_homogeneous_target_set_capability_shapes import fixed_homogeneous_target_set_node_capabilities

    expected = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["land"],
                "supertypes_none": ["basic"], "up_to": 1}
    destruction = {"op": "destroy_targets", "cards": "$targets", "maximum_targets": 1}
    if (FrozenMap(target_schema) != FrozenMap(expected) or type(target_schema["up_to"]) is not int
            or FrozenMap(prefix) != FrozenMap(destruction) or type(prefix["maximum_targets"]) is not int):
        return ()
    try:
        search = optional_target_result_payload_dependencies(result)
    except (TypeError, ValueError):
        return ()
    batch = fixed_homogeneous_target_set_node_capabilities(
        effects=(prefix,), target_schema=target_schema, mechanic_ids=mechanic_ids,
    )
    return tuple(sorted({*search, *batch})) if search and batch else ()


def is_closed_optional_target_result_program(program):
    deps=optional_target_result_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(deps) and set(deps)<=set(program.capability_dependencies)


def optional_target_result_runtime_dependencies(program):
    deps=optional_target_result_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    if not deps:raise ValueError('Optional result requires its complete original target and ordered owners')
    return deps
