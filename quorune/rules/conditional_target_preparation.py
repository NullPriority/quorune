"""Bind the conditional preparation and following damage to identical targets."""
from collections.abc import Mapping
from ..conditional_target_preparation import (
    CONDITIONAL_PREPARATION_CAPABILITY, CONDITIONAL_PREPARATION_MECHANIC,
    validate_preparation_instruction, preparation_branch_capabilities,
)
from ..replacement.immutable import thaw_value


def conditional_prepared_power_capabilities(*, effects, target_schema, mechanic_ids):
    from .creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities
    from ..characteristic_fragments import characteristic_quantity_capabilities
    from ..public_history_query_model import public_history_query_dependencies
    from ..public_zone_condition_model import public_zone_condition_capabilities

    mechanics = set(mechanic_ids)
    if CONDITIONAL_PREPARATION_MECHANIC not in mechanics or len(effects) != 2:
        return ()
    wrapper, damage = effects
    try:predicate, _branch = validate_preparation_instruction(wrapper)
    except (TypeError, ValueError, KeyError):return ()
    if wrapper['player'] != '$controller' or wrapper['condition_target'] != '$target.0':return ()
    schema = thaw_value(target_schema)
    if not isinstance(schema, Mapping) or not isinstance(schema.get('groups'), (list, tuple)) or len(schema['groups']) != 2:return ()
    first = dict(schema['groups'][0]);first.pop('id', None)
    if first != thaw_value(wrapper['target_binding']) or damage.get('source') != '$target.0':return ()
    damage_owners = fixed_creature_power_damage_node_capabilities(effects=(damage,), target_schema=target_schema,
        mechanic_ids=mechanics - {CONDITIONAL_PREPARATION_MECHANIC})
    if not damage_owners:return ()
    owners = {CONDITIONAL_PREPARATION_CAPABILITY, 'resolution.effect.public_condition', *damage_owners}
    for branch, key in (('effects', 'mechanic_ids'), ('otherwise_effects', 'otherwise_mechanic_ids')):
        if not wrapper[branch]:continue
        ids = set(wrapper[key])
        if not ids <= mechanics:return ()
        owners.update(preparation_branch_capabilities(wrapper[branch], ids, first))
        if any(e.get('op') == 'place_counters' and e.get('source') != '$source' for e in wrapper[branch]):return ()
    if predicate.scope == 'night':owners.add('card_form.day_night')
    else:
        raw = predicate.condition.to_dict()
        owners.update(characteristic_quantity_capabilities(raw))
        owners.update(public_history_query_dependencies(raw));owners.update(public_zone_condition_capabilities(raw))
    return tuple(sorted(owners))


def conditional_preparation_runtime_dependencies(program):
    def contains(value):
        if isinstance(value, Mapping):
            return (value.get('op') == 'apply_if_public_condition' and value.get('schema_version') == 16
                or any(contains(child) for child in value.values()))
        return isinstance(value, (list, tuple)) and any(contains(child) for child in value)
    if not contains(program.effects):return ()
    mechanics = {CONDITIONAL_PREPARATION_MECHANIC, 'fixed-creature-power-damage', 'fight', 'cr-115-targets', 'cr-120-damage'}
    for effect in program.effects:
        if isinstance(effect, Mapping):
            mechanics.update(effect.get('mechanic_ids', ()));mechanics.update(effect.get('otherwise_mechanic_ids', ()))
    owners = conditional_prepared_power_capabilities(effects=program.effects, target_schema=program.target_schema, mechanic_ids=mechanics)
    if not owners:raise ValueError('Conditional target preparation needs its complete paired target program')
    return owners
