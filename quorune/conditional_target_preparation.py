"""Rules-owned closure and current reads for conditional target preparation."""
from collections.abc import Mapping
from .conditional_target_preparation_model import (
    PreparationCondition, CONDITIONAL_PREPARATION_VERSION, CONDITIONAL_PREPARATION_MECHANIC,
    CONDITIONAL_PREPARATION_CAPABILITY, validate_preparation_instruction,
)
from .resolution_conditions import ResolutionConditionBinding


def preparation_branch_capabilities(effects, mechanics, target_binding, *, subject='$target.0'):
    from .rules.node_capability_shapes import fixed_target_characteristics_node_capabilities, fixed_counter_placement_node_capabilities

    if not isinstance(effects, (list, tuple)) or not 1 <= len(effects) <= 3:
        return ()
    normalized = []
    for effect in effects:
        if not isinstance(effect, Mapping) or effect.get('card') != subject:
            return ()
        value = {**effect, 'card': '$target.0'}
        if value.get('op') == 'place_counters':
            if value.get('counter') != '+1/+1':return ()
            value['source'] = '$source'
        normalized.append(value)
    owners = fixed_target_characteristics_node_capabilities(effects=tuple(normalized), target_schema=target_binding, mechanic_ids=mechanics)
    if not owners:
        owners = fixed_counter_placement_node_capabilities(effects=tuple(normalized), target_schema=target_binding, mechanic_ids=mechanics)
    if not owners:return ()
    from .rules.capabilities import capability_dependencies_for_node
    return capability_dependencies_for_node(effects=tuple(normalized), target_schema=target_binding, mechanic_ids=mechanics)


def preparation_condition_matches(host, actor, effect):
    predicate, _effects = validate_preparation_instruction(effect)
    subject = effect['condition_target']
    card = next((c for c in host.state.cards.values() if c.ref == subject and c.zone == 'battlefield' and not c.phased_out), None)
    if card is None:return False
    if predicate.scope == 'night':return host.state.day_night == 'night'
    if predicate.scope == 'target':
        return all(host._fixed_public_state_condition_holds(card, leaf, require_available=True)
            for leaf in predicate.condition.leaf_conditions())
    return host._fixed_public_state_condition_holds(ResolutionConditionBinding(controller=actor), predicate.condition, require_available=True)
