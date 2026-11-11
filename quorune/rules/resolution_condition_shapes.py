from __future__ import annotations

"""Full child-shape closure for controller-bound conditional programs."""

from typing import Any, Iterable, Mapping, Sequence

from ..resolution_conditions import (
    CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION,
    RESOLUTION_ALTERNATIVE_SCHEMA_VERSION,
    RESOLUTION_CONDITION_CAPABILITY,
    RESOLUTION_CONDITION_MECHANIC,
    RESOLUTION_CONDITION_OPERATION,
    validate_resolution_condition_instruction,
)
from ..continuous_conditions import FixedPublicStateConditionKind
from ..paid_sacrifice_conditions import (
    PAID_SACRIFICE_CONDITION_CAPABILITY, PAID_SACRIFICE_CONDITION_MECHANIC, PAID_SACRIFICE_CONDITION_VERSION,
)
from .closed_effect_program_shapes import _contains_target_reference
from .casting_additional_costs import fixed_sacrifice_price_matches_subject


def _same_target_result_closed(effects):
    if not 1<=len(effects)<=3:
        return False
    first=effects[0]
    if first.get('op')=='damage':
        return len(effects)==1 and first.get('target')=='$target.0' and first.get('source')=='$source'
    return (first.get('op')=='modify_stats_until_end_of_turn'
        and all(effect.get('op')=='grant_keyword_until_end_of_turn' for effect in effects[1:])
        and all(effect.get('card')=='$target.0' for effect in effects))


def resolution_condition_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
    cost_schema: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    from .capabilities import capability_dependencies_for_node

    if effects and effects[-1].get('schema_version') == 12:
        from .declared_cost_condition_shapes import declared_cost_condition_capabilities
        return declared_cost_condition_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids, cost_schema=cost_schema)

    if effects and effects[-1].get('schema_version')==11:
        from .kicked_target_replacement_shapes import kicked_target_replacement_capabilities
        return kicked_target_replacement_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids,cost_schema=cost_schema)
    if effects and effects[-1].get('schema_version')==10:
        from .kicked_target_shapes import kicked_target_capabilities
        return kicked_target_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids,cost_schema=cost_schema)

    mechanics = set(mechanic_ids)
    if RESOLUTION_CONDITION_MECHANIC not in mechanics or not effects:
        return ()
    wrapper = effects[-1]
    try:
        _condition, conditional = validate_resolution_condition_instruction(wrapper)
        if wrapper.get('schema_version') == CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION:
            from ..token_copy_recipes import copy_recipes
            if not copy_recipes((*conditional, *wrapper.get('otherwise_effects', ()))):
                return ()
    except (ValueError, TypeError, KeyError):
        return ()
    if wrapper["player"] != "$controller":
        return ()
    prefix = effects[:-1]
    if wrapper.get('schema_version') == RESOLUTION_ALTERNATIVE_SCHEMA_VERSION and len(prefix) > 1:
        return ()
    controller_result=any(isinstance(child.get('target'),Mapping) and child['target'].get('kind')=='target_controller_information' for child in conditional)
    if controller_result and (wrapper.get('schema_version')!=6 or len(prefix)!=1
            or prefix[0].get('op')!='damage' or target_schema is None
            or target_schema.get('types_any')!=['creature'] or type(target_schema.get('count')) is not int or target_schema.get('count')!=1):return ()
    otherwise=wrapper.get('otherwise_effects',())
    same_target=wrapper.get('schema_version') in {5,7,8}
    paid_mana=wrapper.get('schema_version') in {6,7}
    paid_sacrifice = wrapper.get("schema_version") == PAID_SACRIFICE_CONDITION_VERSION
    if paid_sacrifice and (
        PAID_SACRIFICE_CONDITION_MECHANIC not in mechanics or wrapper["cast_fact"] != _condition.reference()
        or not fixed_sacrifice_price_matches_subject(cost_schema, _condition.subject)
        or len(prefix) != (0 if otherwise else 1)
    ):
        return ()
    if same_target:
        from ..public_replacement_model import public_replacement_target_closed
        from ..replacement.immutable import thaw_value
        if wrapper.get('schema_version')==8 and thaw_value(wrapper['target_binding'])!=thaw_value(target_schema):return ()
        closed=lambda value:public_replacement_target_closed(value,target_schema) if wrapper.get('schema_version')==8 else _same_target_result_closed(value)
        if (prefix or target_schema is None or type(target_schema.get('count')) is not int
                or target_schema['count']!=1 or not closed(conditional)
                or not closed(otherwise)):
            return ()
    elif _contains_target_reference(conditional) or _contains_target_reference(otherwise):
        return ()
    if len(prefix) + max(len(conditional),len(otherwise)) > 8 or any(
        effect.get("op") == RESOLUTION_CONDITION_OPERATION for effect in prefix
    ):
        return ()
    branches = tuple((branch,ids) for branch,ids in ((prefix,wrapper['prefix_mechanic_ids']),
        (conditional,wrapper['mechanic_ids']),(otherwise,wrapper.get('otherwise_mechanic_ids',()))) if branch)
    if bool(prefix) != bool(wrapper["prefix_mechanic_ids"]) or not set((*wrapper["prefix_mechanic_ids"], *wrapper["mechanic_ids"],*wrapper.get('otherwise_mechanic_ids',()))) <= mechanics:
        return ()
    targeted = tuple(branch for branch,_ids in branches if _contains_target_reference(branch))
    if len(targeted) > (2 if same_target else 1) or (target_schema is not None) != bool(targeted):
        return ()
    from ..public_history_query_model import public_history_query_dependencies
    from ..public_zone_condition_model import public_zone_condition_capabilities

    dependencies = {
        RESOLUTION_CONDITION_CAPABILITY,
        *public_history_query_dependencies(wrapper["condition"]),
        *public_zone_condition_capabilities(wrapper["condition"]),
    }
    if paid_sacrifice:
        dependencies.add(PAID_SACRIFICE_CONDITION_CAPABILITY)
    from ..land_entry_condition_model import land_entry_condition_capabilities
    dependencies.update(land_entry_condition_capabilities(_condition.to_dict()))
    if same_target:
        from ..resolution_conditions import SAME_TARGET_SELF_REPLACEMENT_CAPABILITY,PUBLIC_SAME_TARGET_SELF_REPLACEMENT_CAPABILITY
        dependencies.add(PUBLIC_SAME_TARGET_SELF_REPLACEMENT_CAPABILITY if wrapper.get('schema_version')==8 else SAME_TARGET_SELF_REPLACEMENT_CAPABILITY)
    from ..characteristic_fragments import characteristic_quantity_capabilities
    dependencies.update(characteristic_quantity_capabilities(_condition.to_dict()))
    if wrapper.get('schema_version') in {3,4,5,7,8,9,CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION}:
        from ..resolution_conditions import PUBLIC_SELF_REPLACEMENT_CAPABILITY
        dependencies.add(PUBLIC_SELF_REPLACEMENT_CAPABILITY)
    if wrapper.get('schema_version')==9:
        if wrapper['source_binding']!='$source':return ()
        from ..resolution_conditions import PUBLIC_SOURCE_SELF_REPLACEMENT_CAPABILITY
        dependencies.add(PUBLIC_SOURCE_SELF_REPLACEMENT_CAPABILITY)
    if paid_mana:
        from ..spell_mana_conditions import PAID_MANA_CONDITION_CAPABILITY,PAID_MANA_CONDITION_MECHANIC
        from ..spell_mana_counts import MANA_SPENT_COUNTS_CAPABILITY
        if PAID_MANA_CONDITION_MECHANIC not in mechanics or wrapper['cast_fact']!=_condition.reference():return ()
        dependencies.update((PAID_MANA_CONDITION_CAPABILITY,MANA_SPENT_COUNTS_CAPABILITY))
    if _condition.kind == FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS:
        dependencies.add('condition.creature.fixed_numeric_exists')
    if wrapper.get('schema_version') in {2,4,5}:
        from ..resolution_conditions import CAST_FACT_CONDITION_CAPABILITY, CAST_FACT_CONDITION_MECHANIC
        if CAST_FACT_CONDITION_MECHANIC not in mechanics or wrapper['cast_fact'] != '$context.kicked':
            return ()
        dependencies.add(CAST_FACT_CONDITION_CAPABILITY)
    ambient_mechanics = mechanics - {
        RESOLUTION_CONDITION_MECHANIC,
        'fixed-cast-fact-condition',
        'fixed-paid-mana-resolution-condition',
        PAID_SACRIFICE_CONDITION_MECHANIC,
        *wrapper["prefix_mechanic_ids"],
        *wrapper["mechanic_ids"],
        *wrapper.get('otherwise_mechanic_ids',()),
    }
    for branch,branch_mechanics in branches:
        child = capability_dependencies_for_node(
            effects=branch,
            target_schema=target_schema if branch in targeted else None,
            mechanic_ids=(*branch_mechanics, *ambient_mechanics),
            cost_schema=cost_schema,
        )
        if not child:
            return ()
        dependencies.update(child)
    return tuple(sorted(dependencies))


def bound_resolution_condition_runtime_dependencies(program):
    bound_versions = {5, 8, 9, 10, 11, RESOLUTION_ALTERNATIVE_SCHEMA_VERSION, PAID_SACRIFICE_CONDITION_VERSION}
    if not any(effect.get("op") == RESOLUTION_CONDITION_OPERATION and effect.get("schema_version") in bound_versions
               for effect in program.effects):
        return ()
    dependencies=resolution_condition_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage,cost_schema=program.cost_schema)
    if not dependencies:
        raise ValueError('Bound resolution condition lacks complete branch and target closure')
    return dependencies
