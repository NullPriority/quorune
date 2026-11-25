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

def resolution_target_color_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..conditional_target_preparation_model import (
        RESOLUTION_TARGET_COLOR_CAPABILITY, RESOLUTION_TARGET_COLOR_FIELD, RESOLUTION_TARGET_COLOR_MECHANIC,
        resolution_target_color_primitive,
    )
    from ..replacement.immutable import thaw_value
    from .node_capability_shapes import targeted_counter_node_capabilities, targeted_destruction_node_capabilities

    if RESOLUTION_TARGET_COLOR_MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    effect = effects[0]
    try:
        field, _color = resolution_target_color_primitive(effect)
    except (TypeError, ValueError):
        return ()
    if field == "stack":
        expected = {"zones": ["stack"], "categories": ["spell"], "count": 1, "source_exclusion": True}
        resolve = targeted_counter_node_capabilities
    else:
        expected = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}
        resolve = targeted_destruction_node_capabilities
    if thaw_value(target_schema) != expected:
        return ()
    primitive = {key: value for key, value in effect.items() if key != RESOLUTION_TARGET_COLOR_FIELD}
    required = resolve(effects=(primitive,), target_schema=target_schema, mechanic_ids=mechanic_ids)
    return (*required, RESOLUTION_TARGET_COLOR_CAPABILITY) if required else ()


def resolution_target_color_program_dependencies(program):
    from ..conditional_target_preparation_model import (
        RESOLUTION_TARGET_COLOR_CAPABILITY, RESOLUTION_TARGET_COLOR_FIELD, RESOLUTION_TARGET_COLOR_MECHANIC,
    )
    from .effect_shape_walkers import nested_effects

    guarded = tuple(effect for effect in nested_effects((program.effects, program.target_schema, program.handlers))
                    if RESOLUTION_TARGET_COLOR_FIELD in effect)
    if not guarded:
        return ()
    if (program.event != "resolve" or program.active_zone != "stack"
            or program.event_condition is not None or program.handlers
            or not program.ability_id.startswith("spell:")):
        raise ValueError("A current-color result requires an ordinary resolving spell body")
    schema = program.target_schema
    modes = schema.get("modes") if isinstance(schema, Mapping) else None
    if isinstance(modes, Mapping):
        from .modal_node_capabilities import modal_schema_runtime_dependencies

        children = tuple(effect for mode in modes.values() if isinstance(mode, Mapping)
                         for effect in mode.get("effects", ()) if isinstance(effect, Mapping)
                         and RESOLUTION_TARGET_COLOR_FIELD in effect)
        required = modal_schema_runtime_dependencies(schema) if not program.effects and children == guarded else ()
    else:
        primitive = guarded[0]
        leaf_mechanic = "counter" if primitive.get("op") == "counter_stack_target" else "destroy"
        required = resolution_target_color_node_capabilities(
            effects=program.effects, target_schema=schema,
            mechanic_ids=(RESOLUTION_TARGET_COLOR_MECHANIC, leaf_mechanic, "cr-115-targets"),
        )
    if RESOLUTION_TARGET_COLOR_CAPABILITY not in required:
        raise ValueError("The current-color result has an unowned instruction or target scope")
    return required


def bind_resolution_target_color(host, item, raw_effect, effect):
    """Check the current color of the same revalidated target before applying its result."""
    from ..conditional_target_preparation_model import RESOLUTION_TARGET_COLOR_FIELD, resolution_target_color_primitive

    if RESOLUTION_TARGET_COLOR_FIELD not in raw_effect:
        return effect
    field, color = resolution_target_color_primitive(raw_effect)
    if (item.kind not in {"spell", "spell_copy"}
            or item.context.get("targets_revalidated") is not True
            or item.context.get("currently_resolving") is not True):
        raise ValueError("The color check requires one revalidated target of the resolving spell")
    ref = effect[field]
    if ref is None:
        return None
    if type(ref) is not str or tuple(item.targets) != (ref,):
        raise ValueError("The color check changed the original announced target")
    if field == "stack":
        target = next((target for target in host.state.stack if target.ref == ref), None)
        if target is None or target.kind not in {"spell", "spell_copy"}:
            return None
        card = host.state.cards.get(target.card_object_id)
        if card is None:
            return None
    else:
        card = host._resolve_object(item.controller, ref, zones={"battlefield"})
        if card.phased_out:
            return None
    if color not in host._effective_card_data(card).get("colors", ()):
        return None
    return {key: value for key, value in effect.items() if key != RESOLUTION_TARGET_COLOR_FIELD}
