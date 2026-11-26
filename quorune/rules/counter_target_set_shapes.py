from __future__ import annotations

"""Versioned target-set counter payloads retain scalar predicate/amount owners."""

from collections.abc import Mapping
from ..counter_names import EXISTING_COUNTER_AMOUNT,is_existing_counter_amount
from ..target_cardinality import validate_selected_target_count
from ..replacement.immutable import FrozenMap
from ..split_two_counter_target_amount import (
    SPLIT_TWO_COUNTER_TARGET_AMOUNT_KIND, SPLIT_TWO_COUNTER_TARGET_CAPABILITY, SplitTwoCounterTargetAmount,
    is_optional_split_two_counter_amount,
)

QUANTIFIED_COUNTER_CAPABILITY='counter.producer.quantified_target_set'
QUANTIFIED_COUNTER_MECHANIC='quantified-counter-target-set'


from ..source_entry_cast_information import (
    PAID_WHITE_ENTRY_TARGET_CAPABILITY, PAID_WHITE_ENTRY_TARGET_MECHANIC, original_entry_white_pairs,
    uses_paid_white_entry_amount, validate_paid_white_entry_amount,
)
from ..spell_mana_counts import MANA_SPENT_COUNTS_CAPABILITY


def _paid_white_target_schema_is_closed(schema):
    return FrozenMap(schema) == FrozenMap({
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
        "up_to": 2, "controller_relation": "you", "source_exclusion": True,
    }) and type(schema.get("up_to")) is int and schema.get("source_exclusion") is True


def uses_split_two_counter_amount(effect):
    amount = effect.get("amount")
    return (effect.get("op") == "place_counters_on_targets" and isinstance(amount, Mapping)
            and amount.get("kind") == SPLIT_TWO_COUNTER_TARGET_AMOUNT_KIND)


def _split_two_target_schema_is_closed(schema, *, allows_zero_targets):
    creature = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"]}
    if allows_zero_targets:
        return type(schema.get("up_to")) is int and FrozenMap(schema) == FrozenMap({**creature, "up_to": 2})
    if type(schema.get("min")) is not int or type(schema.get("max")) is not int:
        return False
    base = {**creature, "min": 1, "max": 2}
    return FrozenMap(schema) in (FrozenMap(base), FrozenMap({**base, "controller_relation": "you"}))


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
    split = uses_split_two_counter_amount(effect)
    if split:
        spec = SplitTwoCounterTargetAmount.from_dict(amount)
        if (resolved or variable or maximum != 2 or name not in {"+1/+1", "-1/-1"}
                or spec.allows_zero_targets and name != "+1/+1"):
            raise ValueError("Two-counter distribution requires its exact original target set")
    elif uses_paid_white_entry_amount(amount):
        validate_paid_white_entry_amount(amount)
        if resolved or variable or maximum != 2 or name != "+1/+1":
            raise ValueError("Paid white pairs require their exact original +1/+1 target set")
    elif not relative and (type(amount) is not int or amount<=0):
        raise ValueError('Counter amount must be fixed positive or existing named count')
    if not resolved and (effect['cards']!='$targets' or effect['source']!='$source'):raise ValueError('Counter targets require their selected set and source')
    return variable,relative


def quantified_counter_target_capabilities(*,effects,target_schema,mechanic_ids):
    if QUANTIFIED_COUNTER_MECHANIC not in mechanic_ids or len(effects)!=1 or not isinstance(target_schema,Mapping):return ()
    effect=effects[0]
    try:
        variable,relative=validate_quantified_counter_target_effect(effect)
        if variable:return ()
        split = uses_split_two_counter_amount(effect)
        paid_white = uses_paid_white_entry_amount(effect["amount"])
        if ((PAID_WHITE_ENTRY_TARGET_MECHANIC in mechanic_ids) != paid_white
                or paid_white and not _paid_white_target_schema_is_closed(target_schema)):
            return ()
        if split and not _split_two_target_schema_is_closed(
            target_schema, allows_zero_targets=is_optional_split_two_counter_amount(effect["amount"]),
        ):
            return ()
        from .fixed_homogeneous_target_set_capability_shapes import _singular_schema,_target_bounds
        normalized=_singular_schema(target_schema);bounds=_target_bounds(target_schema)
        if normalized is None or bounds is None or normalized[1] or bounds[1]!=effect['maximum_targets']:return ()
        scalar={k:v for k,v in effect.items() if k not in {'schema_version','cards','maximum_targets'}}
        scalar.update(op='place_counters',card='$target.0')
        if split or paid_white:
            scalar["amount"] = 1
        from .counter_placement_capability_shapes import fixed_counter_placement_node_capabilities
        owned=fixed_counter_placement_node_capabilities(effects=(scalar,),target_schema=normalized[0],mechanic_ids=mechanic_ids)
        if not owned:return ()
        from .capabilities import capability_dependencies_for_node
        ambient=capability_dependencies_for_node(effects=(scalar,),target_schema=normalized[0],
            mechanic_ids=set(mechanic_ids)-{QUANTIFIED_COUNTER_MECHANIC,'any-number-target-set',PAID_WHITE_ENTRY_TARGET_MECHANIC})
        required = {*owned, *ambient, QUANTIFIED_COUNTER_CAPABILITY,
                    "counter.producer.fixed_permanent_target_set_effect", "target.revalidate_resolution"}
        if split:
            required.add(SPLIT_TWO_COUNTER_TARGET_CAPABILITY)
        if paid_white:
            required.update((PAID_WHITE_ENTRY_TARGET_CAPABILITY, MANA_SPENT_COUNTS_CAPABILITY))
        return tuple(sorted(required))
    except (ValueError,TypeError,KeyError):return ()


def quantified_counter_payload_dependencies(value):
    if value.get('op')!='place_counters_on_targets' or 'schema_version' not in value:return ()
    _variable,relative=validate_quantified_counter_target_effect(value)
    use_split = (SPLIT_TWO_COUNTER_TARGET_CAPABILITY,) if uses_split_two_counter_amount(value) else ()
    paid_white = ((PAID_WHITE_ENTRY_TARGET_CAPABILITY, MANA_SPENT_COUNTS_CAPABILITY)
                  if uses_paid_white_entry_amount(value["amount"]) else ())
    return (QUANTIFIED_COUNTER_CAPABILITY,'counter.producer.fixed_permanent_target_set_effect',
            *(('counter.producer.named_doubling',) if relative else ()), *use_split, *paid_white)


def bind_paid_white_entry_target_amount(effect, item):
    amount = effect.get("amount")
    if not uses_paid_white_entry_amount(amount):
        return effect
    validate_quantified_counter_target_effect(effect)
    return {**effect, "amount": original_entry_white_pairs(amount, item)}


def _split_two_counter_modal_dependencies(program):
    from .effect_shape_walkers import nested_effects

    schema = program.target_schema
    modes = schema.get("modes") if isinstance(schema, Mapping) else None
    if not isinstance(modes, Mapping):
        return ()
    split_modes = []
    for mode in modes.values():
        if not isinstance(mode, Mapping) or not isinstance(mode.get("effects"), (list, tuple)):
            continue
        effects = tuple(mode["effects"])
        if any(uses_split_two_counter_amount(child) for child in nested_effects(effects)):
            split_modes.append(effects)
    if not split_modes:
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack") or program.effects or program.handlers
            or type(schema.get("mode_count")) is not int or schema["mode_count"] != 1
            or len(split_modes) != 1 or len(split_modes[0]) != 1
            or not uses_split_two_counter_amount(split_modes[0][0])
            or is_optional_split_two_counter_amount(split_modes[0][0]["amount"])):
        raise ValueError("Modal two-counter distribution requires one complete selected mode of an ordinary choose-one spell")
    from .modal_node_capabilities import modal_schema_runtime_dependencies

    required = modal_schema_runtime_dependencies(schema)
    if SPLIT_TWO_COUNTER_TARGET_CAPABILITY not in required:
        raise ValueError("Two-counter distribution must retain authoritative admission of all printed modes")
    return required


def split_two_counter_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if modal := _split_two_counter_modal_dependencies(program):
        return modal
    marked = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                   if uses_split_two_counter_amount(effect))
    if not marked and SPLIT_TWO_COUNTER_TARGET_CAPABILITY not in program.capability_dependencies:
        return ()
    if any(is_optional_split_two_counter_amount(effect["amount"]) for effect in marked):
        from .monarch_counter_distribution import monarch_counter_distribution_program_dependencies

        required = monarch_counter_distribution_program_dependencies(program)
        if not required:
            raise ValueError("An optional split requires its complete original ordered upkeep")
        return required
    if len(marked) != 1 or tuple(program.effects) != marked:
        raise ValueError("Two-counter distribution requires one complete original target set")
    if program.handlers:
        from ..semantic_runtime.activated_abilities import (
            ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
        )

        if ((program.event, program.active_zone) != ("activate", "battlefield")
                or len(program.handlers) != 1
                or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
            raise ValueError("Two-counter distribution permits only its original activation catalog")
        abilities = activated_abilities_from_descriptors(program.handlers)
        if len(abilities) != 1 or program.ability_id != f"ability:{abilities[0].ability_id}":
            raise ValueError("Two-counter distribution lost its original activation identity")
    required = quantified_counter_target_capabilities(
        effects=marked, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if SPLIT_TWO_COUNTER_TARGET_CAPABILITY not in required:
        raise ValueError("Two-counter distribution requires its original target and amount authorities")
    return required


def bind_split_two_counter_allocation(effect, item):
    if not uses_split_two_counter_amount(effect):
        return effect
    if item.context.get("targets_revalidated") is not True:
        raise ValueError("Two-counter distribution must use the original revalidated target slots")
    symbolic = effect
    if effect.get("cards") != "$targets":
        groups = item.context.get("target_groups_current")
        if (item.kind not in {"spell", "spell_copy"} or len(item.modes) != 1
                or not isinstance(groups, Mapping) or len(groups) != 1):
            raise ValueError("A projected distribution needs its one original mode target group")
        (group, slots), = groups.items()
        if (effect["cards"] != f"$target_group.{group}" or not isinstance(slots, (list, tuple))
                or tuple(slots) != tuple(item.targets)):
            raise ValueError("The selected two-counter mode changed its original target group")
        symbolic = {**effect, "cards": "$targets"}
    validate_quantified_counter_target_effect(symbolic)
    spec = SplitTwoCounterTargetAmount.from_dict(effect["amount"])
    return {**effect, "amount": spec.per_target(len(item.targets))}


def is_closed_quantified_counter_target_program(program):
    if QUANTIFIED_COUNTER_MECHANIC not in program.coverage:return False
    from .any_number_target_shapes import any_number_target_node_capabilities
    required=(any_number_target_node_capabilities if 'any-number-target-set' in program.coverage else quantified_counter_target_capabilities)(
        effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
