from __future__ import annotations

"""Fixed characteristic results over one homogeneous revalidated target set."""

from typing import Any,Mapping,Sequence
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..keyword_counters import keyword_counter_mechanic
from ..next_turn_effect import NEXT_TURN_EFFECT_FIELD, next_turn_effect_dependencies, split_next_turn_effect
from ..source_tap_effect import SOURCE_TAP_EFFECT_FIELD, source_tap_effect_dependencies, split_source_tap_effect
from .power_toughness_switch import (
    SWITCH_CAPABILITY, SWITCH_MECHANIC, is_power_toughness_switch, power_toughness_switch_runtime_dependencies,
)
from .fixed_stat_multipliers import (
    STAT_MULTIPLIER_CAPABILITY, STAT_MULTIPLIER_MECHANIC,
    is_fixed_stat_multiplier, fixed_stat_multiplier_runtime_dependencies,
)

TARGET_CHARACTERISTIC_SET_CAPABILITY='continuous.resolution.fixed_target_characteristic_set'
OPERATION='apply_source_characteristics_until_end_of_turn'
RESOLUTION_STAT_MECHANIC_CAPABILITIES = {
    SWITCH_MECHANIC: (SWITCH_CAPABILITY,),
    STAT_MULTIPLIER_MECHANIC: (STAT_MULTIPLIER_CAPABILITY,),
}


def decode_target_characteristics(effect,*,allow_zero_result=False):
    if not isinstance(effect, Mapping):
        raise ValueError('Target characteristic set fields are malformed')
    effect, _ = split_source_tap_effect(effect)
    effect, _ = split_next_turn_effect(effect)
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
    if (NEXT_TURN_EFFECT_FIELD in effect or SOURCE_TAP_EFFECT_FIELD in effect) and type(target_schema.get('count')) is int and target_schema['count'] == 1:
        if effect['maximum_targets'] != 1:
            return ()
        singular = target_schema
    else:
        parsed = _singular_schema(target_schema)
        bounds = _target_bounds(target_schema)
        if parsed is None or bounds is None or parsed[1] or bounds[1] != effect['maximum_targets']:
            return ()
        singular = parsed[0]
    requires_creature = singular.get('types_any') == ['creature'] or (
        SOURCE_TAP_EFFECT_FIELD in effect and 'creature' in singular.get('types_all', ())
    )
    if not requires_creature or not direct_permanent_target_schema_is_closed(singular):return ()
    if not {keyword_counter_mechanic(k) or k.casefold() for k in keywords}<=mechanics:return ()
    return tuple(sorted({TARGET_CHARACTERISTIC_SET_CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn','target.revalidate_resolution',
        *next_turn_effect_dependencies(effect), *source_tap_effect_dependencies(effect), *direct_target_predicate_capabilities(singular),
        *(cap for keyword in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])}))


def is_closed_target_characteristic_set_program(program):
    required=target_characteristic_set_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)


def characteristic_set_runtime_dependencies(program):
    """Payload-owned minimums also apply to composed and modal instructions."""
    dependencies=set()
    def visit(value,target_schema,coverage):
        if isinstance(value,Mapping):
            definitions=value.get('modes')
            if isinstance(definitions,Mapping) and any(isinstance(d,Mapping) and 'mechanics' in d for d in definitions.values()):
                from .modal_capability_shapes import fixed_choose_one_modal_branches,fixed_nonrepeating_modal_branches
                from ..compiler.modal_templates import FIXED_CHOOSE_ONE_MODAL_MECHANIC,FIXED_NONREPEATING_MODAL_MECHANIC
                nonrepeating='min_modes' in value
                owner=fixed_nonrepeating_modal_branches if nonrepeating else fixed_choose_one_modal_branches
                mechanics={FIXED_NONREPEATING_MODAL_MECHANIC if nonrepeating else FIXED_CHOOSE_ONE_MODAL_MECHANIC}
                for d in definitions.values():
                    if not isinstance(d,Mapping) or not isinstance(d.get('mechanics'),(list,tuple)):
                        raise ValueError('Modal characteristic result lacks its closed branch')
                    mechanics.update(d['mechanics'])
                branches=owner(effects=(),target_schema=value,mechanic_ids=tuple(sorted(mechanics)))
                if branches is None:raise ValueError('Modal characteristic result has a malformed branch')
                for branch in branches:visit(branch.effects,branch.target_schema,branch.mechanics)
                return
            if value.get('op')==OPERATION and value.get('schema_version')==6:
                fixed=value
                if 'announced_target_count' in value:
                    parameter = target_schema.get('count_parameter') if isinstance(target_schema, Mapping) else None
                    if isinstance(parameter, Mapping) and parameter.get('parameter') == 'x':
                        from .fixed_homogeneous_target_set_capability_shapes import announced_target_set_node_capabilities
                        owner = announced_target_set_node_capabilities
                    else:
                        from .any_number_target_shapes import any_number_target_node_capabilities
                        owner = any_number_target_node_capabilities
                    required=owner(effects=(value,),target_schema=target_schema,mechanic_ids=coverage)
                    if not required:raise ValueError('Variable characteristic result lacks its cardinality owner')
                    dependencies.update(required)
                    fixed={key:child for key,child in value.items() if key!='announced_target_count'}
                    fixed['maximum_targets']=2
                if any(isinstance(value.get(field),Mapping) for field in ('power','toughness')):
                    from ..compiler.public_query_effect_amounts import public_query_amount_shape_context
                    context=public_query_amount_shape_context((value,),set(coverage),target_schema=target_schema)
                    if context is None:raise ValueError('Target characteristic amount lacks its existing quantity owner')
                    fixed=context[0][0]
                keywords=decode_target_characteristics(fixed)
                dependencies.update((TARGET_CHARACTERISTIC_SET_CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn','target.revalidate_resolution'))
                dependencies.update(cap for keyword in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])
            elif is_power_toughness_switch(value):
                dependencies.update(power_toughness_switch_runtime_dependencies(value, target_schema=target_schema))
            elif is_fixed_stat_multiplier(value):
                dependencies.update(fixed_stat_multiplier_runtime_dependencies(value, target_schema=target_schema))
            for child in value.values():visit(child,target_schema,coverage)
        elif isinstance(value,(list,tuple)):
            for child in value:visit(child,target_schema,coverage)
    visit((program.effects,program.target_schema),program.target_schema,program.coverage)
    return tuple(sorted(dependencies))
