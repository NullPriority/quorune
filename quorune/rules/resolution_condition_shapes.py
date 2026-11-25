from __future__ import annotations

"""Full child-shape closure for controller-bound conditional programs."""

from typing import Any, Iterable, Mapping, Sequence

from ..resolution_conditions import (
    GREATEST_POWER_TARGET_DISCARD_VERSION,
    CONDITIONAL_COPY_REFERENCE_SCHEMA_VERSION,
    RESOLUTION_ALTERNATIVE_SCHEMA_VERSION,
    RESOLUTION_CONDITION_CAPABILITY,
    RESOLUTION_CONDITION_MECHANIC,
    RESOLUTION_CONDITION_OPERATION,
    validate_resolution_condition_instruction,
)
from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateFact
from ..ability_resolution_ordinal import is_second_ability_draw, is_third_ability_result
from ..other_spell_history_model import is_other_spell_draw
from .other_spell_history_shapes import other_spell_draw_node_capabilities, other_spell_draw_runtime_dependencies
from .ability_resolution_ordinal_shapes import (
    second_ability_draw_node_capabilities, second_ability_draw_runtime_dependencies,
    third_ability_result_node_capabilities, third_ability_result_runtime_dependencies,
)
from ..replacement.immutable import thaw_value
from ..paid_sacrifice_conditions import (
    PAID_SACRIFICE_CONDITION_CAPABILITY, PAID_SACRIFICE_CONDITION_MECHANIC, PAID_SACRIFICE_CONDITION_VERSION,
    ACTIVATION_PAID_LEGENDARY_AMASS_CAPABILITY, ACTIVATION_PAID_SUBTYPE_CAPABILITY, ACTIVATION_PAID_THRULL_COUNTERS_CAPABILITY,
    is_activation_paid_creature_reference, activation_paid_legendary_amass_result_is_closed,
    ACTIVATION_PAID_SUSPECTED_DRAW_CAPABILITY, PAID_SUSPECTED_CONDITION_KIND, activation_paid_suspected_draw_is_closed,
    activation_paid_subtype_result_is_closed, activation_paid_thrull_counters_result_is_closed,
)
from .closed_effect_program_shapes import _contains_target_reference


def _same_target_result_closed(effects):
    if not 1<=len(effects)<=3:
        return False
    first=effects[0]
    if first.get('op')=='damage':
        return len(effects)==1 and first.get('target')=='$target.0' and first.get('source')=='$source'
    return (first.get('op')=='modify_stats_until_end_of_turn'
        and all(effect.get('op')=='grant_keyword_until_end_of_turn' for effect in effects[1:])
        and all(effect.get('card')=='$target.0' for effect in effects))


def _plain_target_copy_token():
    from ..token_copy_recipes import TokenCopyRecipeSpec

    return {
        "op": "create_token", "controller": "$controller", "quantity": 1,
        "copy_spec": TokenCopyRecipeSpec("target").to_dict(),
    }


def _same_target_copy_count_closed(conditional, otherwise):
    if len(conditional) != 1 or len(otherwise) != 1:
        return False

    normal = _plain_target_copy_token()
    kicked_count = conditional[0].get("quantity")
    return (
        type(kicked_count) is int and 2 <= kicked_count <= 5
        and thaw_value(otherwise[0]) == normal
        and thaw_value(conditional[0]) == {**normal, "quantity": kicked_count}
    )


def _same_target_condition_branches_closed(wrapper, conditional, otherwise, target_schema):
    if wrapper.get("schema_version") == 8:
        from ..public_replacement_model import public_replacement_target_closed

        return thaw_value(wrapper["target_binding"]) == thaw_value(target_schema) and all(
            public_replacement_target_closed(branch, target_schema) for branch in (conditional, otherwise)
        )
    if wrapper.get("schema_version") == 5:
        from ..compiler.bound_effect_programs import BOUND_EFFECT_PROGRAM_MECHANIC, CLOSED_EFFECT_PROGRAM_MECHANIC
        from ..compiler.direct_target import DirectPermanentTargetSpec

        schema = DirectPermanentTargetSpec(types_any=("creature",)).to_target_schema()
        if thaw_value(target_schema) == schema and _same_target_copy_count_closed(conditional, otherwise):
            return True
        ordered = {BOUND_EFFECT_PROGRAM_MECHANIC, CLOSED_EFFECT_PROGRAM_MECHANIC}
        scoped = all(ordered <= set(wrapper[field]) for field in ("mechanic_ids", "otherwise_mechanic_ids"))
        if target_schema == schema and scoped and all(
            len(branch) == 2 and branch[0] == {"op": "untap", "card": "$target.0"}
            and branch[1].get("op") == "modify_stats_until_end_of_turn" and branch[1].get("card") == "$target.0"
            for branch in (conditional, otherwise)
        ):
            return True
    return _same_target_result_closed(conditional) and _same_target_result_closed(otherwise)


def _greatest_power_otherwise_counter(branch, condition, conditional, target_schema, prefix):
    from ..counter_placement_targets import controlled_creature_counter_choice_dependencies

    if (condition.fact is not FixedPublicStateFact.CONTROLS_A_GREATEST_POWER_CREATURE
            or target_schema is not None or prefix or len(branch) != 1
            or tuple(conditional) != ({"op": "draw", "player": "$controller", "count": 1, "private": True},)):
        return ()
    try:
        return controlled_creature_counter_choice_dependencies(branch[0])
    except (TypeError, ValueError):
        return ()


def _ordinary_targeted_branches(wrapper, prefix, branches):
    target_copy_prefix = (
        wrapper.get("schema_version") is None and len(prefix) == 1
        and prefix[0].get("op") == "create_token" and thaw_value(prefix[0]) == _plain_target_copy_token()
    )
    return tuple(
        branch for branch, _ids in branches
        if _contains_target_reference(branch) or (branch is prefix and target_copy_prefix)
    )


def _paid_sacrifice_instruction_scope_is_closed(cost_schema, condition, reference, prefix, children, otherwise, target_schema):
    if reference == condition.reference():
        from .casting_additional_costs import fixed_sacrifice_price_matches_subject

        return fixed_sacrifice_price_matches_subject(cost_schema, condition.subject)
    from .activation.paid_characteristic_price import activation_paid_creature_choice, activation_paid_suspected_choice

    if condition.kind == PAID_SUSPECTED_CONDITION_KIND:
        return (activation_paid_suspected_choice(cost_schema) is not None
                and activation_paid_suspected_draw_is_closed(condition, reference, prefix, children, otherwise, target_schema))

    legendary = condition.trait == "legendary"
    if activation_paid_creature_choice(cost_schema, legendary=legendary) is None:
        return False
    if legendary:
        return activation_paid_legendary_amass_result_is_closed(condition, reference, prefix, children, target_schema)
    return target_schema is None and (
        activation_paid_subtype_result_is_closed(condition, reference, prefix, children)
        or cost_schema.get("activation_limit") == "once_per_turn"
        and activation_paid_thrull_counters_result_is_closed(condition, reference, prefix, children)
    )


def _paid_sacrifice_capability(condition, reference, prefix, children):
    if not is_activation_paid_creature_reference(reference):
        return PAID_SACRIFICE_CONDITION_CAPABILITY
    if condition.kind == PAID_SUSPECTED_CONDITION_KIND:
        return ACTIVATION_PAID_SUSPECTED_DRAW_CAPABILITY
    if condition.trait == "legendary":
        return ACTIVATION_PAID_LEGENDARY_AMASS_CAPABILITY
    if activation_paid_thrull_counters_result_is_closed(condition, reference, prefix, children):
        return ACTIVATION_PAID_THRULL_COUNTERS_CAPABILITY
    return ACTIVATION_PAID_SUBTYPE_CAPABILITY


def resolution_condition_node_capabilities(
    *, effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
    cost_schema: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    from .capabilities import capability_dependencies_for_node

    if any(is_other_spell_draw(effect) for effect in effects):
        return other_spell_draw_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if any(is_third_ability_result(effect) for effect in effects):
        return third_ability_result_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if any(is_second_ability_draw(effect) for effect in effects):
        return second_ability_draw_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if effects and effects[-1].get('op')==RESOLUTION_CONDITION_OPERATION and effects[-1].get('schema_version')==18:
        from .paid_alternative_cast_facts import paid_alternative_result_capabilities
        return paid_alternative_result_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)

    if effects and effects[-1].get('schema_version') == 17:
        from .target_replacements import current_target_replacement_capabilities
        return current_target_replacement_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
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
        PAID_SACRIFICE_CONDITION_MECHANIC not in mechanics
        or not _paid_sacrifice_instruction_scope_is_closed(
            cost_schema, _condition, wrapper["cast_fact"], prefix, conditional, otherwise, target_schema,
        )
        or len(prefix) != (0 if otherwise else 1)
    ):
        return ()
    if same_target:
        if (prefix or target_schema is None or type(target_schema.get('count')) is not int
                or target_schema['count']!=1
                or not _same_target_condition_branches_closed(wrapper, conditional, otherwise, target_schema)):
            return ()
    elif wrapper.get("schema_version") == GREATEST_POWER_TARGET_DISCARD_VERSION:
        from ..compiler.affected_player_discard_templates import (
            AffectedPlayerDiscardSubject, FixedAffectedPlayerDiscardTemplate,
        )

        discard = FixedAffectedPlayerDiscardTemplate(AffectedPlayerDiscardSubject.TARGET_OPPONENT, 1)
        if (prefix or target_schema is None or type(target_schema.get("count")) is not int
                or thaw_value(target_schema) != discard.target_schema
                or tuple(wrapper["mechanic_ids"]) != discard.mechanics):
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
    targeted = (conditional, otherwise) if same_target else _ordinary_targeted_branches(wrapper, prefix, branches)
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
        dependencies.add(_paid_sacrifice_capability(_condition, wrapper["cast_fact"], prefix, conditional))
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
    from ..counter_placement_targets import CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD

    for branch,branch_mechanics in branches:
        if any(CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD in child for child in branch):
            if branch is not otherwise or "cr-122-counters" not in branch_mechanics:
                return ()
            child = _greatest_power_otherwise_counter(branch, _condition, conditional, target_schema, prefix)
        else:
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
    from .activation.paid_characteristic_results import activation_paid_characteristic_runtime_dependencies

    if activation_characteristic := activation_paid_characteristic_runtime_dependencies(program):
        return activation_characteristic
    ordinal = (other_spell_draw_runtime_dependencies(program) or second_ability_draw_runtime_dependencies(program)
               or third_ability_result_runtime_dependencies(program))
    if ordinal:
        return ordinal
    from .paid_alternative_cast_facts import paid_alternative_program_dependencies
    paid_alternative=paid_alternative_program_dependencies(program)
    if paid_alternative:return paid_alternative
    if any(effect.get('op') == RESOLUTION_CONDITION_OPERATION and effect.get('schema_version') == 17
           for effect in program.effects):
        from .target_replacements import current_target_replacement_capabilities
        from ..target_replacement_model import CURRENT_TARGET_REPLACEMENT_MECHANIC
        mechanics = {CURRENT_TARGET_REPLACEMENT_MECHANIC, RESOLUTION_CONDITION_MECHANIC}
        for effect in program.effects:
            mechanics.update(effect.get('mechanic_ids', ()))
            mechanics.update(effect.get('otherwise_mechanic_ids', ()))
        dependencies = current_target_replacement_capabilities(effects=program.effects,
            target_schema=program.target_schema, mechanic_ids=mechanics)
        if not dependencies:
            raise ValueError('Current target replacement needs both complete owned branches and its original target')
        return dependencies
    if any(e.get('op') == RESOLUTION_CONDITION_OPERATION and e.get('schema_version') == 3 for e in program.effects):
        effects = program.effects
        intrinsic_dependencies = ()
        mechanics = {RESOLUTION_CONDITION_MECHANIC, *program.coverage}
        if effects and effects[0].get('op') == 'ascend':
            from .city_blessing_shapes import ascend_spell_node_capabilities
            intrinsic_dependencies = ascend_spell_node_capabilities(
                effects=(effects[0],), target_schema=None, mechanic_ids=program.coverage)
            if not intrinsic_dependencies or program.event != 'resolve' or program.active_zone != 'stack':
                raise ValueError('Intrinsic Ascend prefix requires its complete spell declaration')
            effects = effects[1:]
            mechanics.discard('ascend')
        for effect in effects:
            if effect.get('op') == RESOLUTION_CONDITION_OPERATION:
                for field in ('mechanic_ids', 'otherwise_mechanic_ids', 'prefix_mechanic_ids'):
                    mechanics.update(effect.get(field, ()))
        dependencies = resolution_condition_node_capabilities(effects=effects, target_schema=program.target_schema,
            mechanic_ids=mechanics, cost_schema=program.cost_schema)
        if not dependencies:raise ValueError('Public self-replacement requires complete branches and controller binding')
        return tuple(sorted({*intrinsic_dependencies, *dependencies}))
    bound_versions = {5, 8, 9, 10, 11, 17, RESOLUTION_ALTERNATIVE_SCHEMA_VERSION, PAID_SACRIFICE_CONDITION_VERSION,
                      GREATEST_POWER_TARGET_DISCARD_VERSION}
    if not any(effect.get("op") == RESOLUTION_CONDITION_OPERATION and effect.get("schema_version") in bound_versions
               for effect in program.effects):
        return ()
    dependencies=resolution_condition_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage,cost_schema=program.cost_schema)
    if not dependencies:
        raise ValueError('Bound resolution condition lacks complete branch and target closure')
    return dependencies
