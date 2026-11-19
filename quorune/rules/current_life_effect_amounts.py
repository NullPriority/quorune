"""Bind the locked controller's life once through the existing typed reader."""
from collections.abc import Mapping
from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..current_life_effect_amount_model import (CurrentLifeEffectAmountSpec,
    CURRENT_LIFE_AMOUNT_KIND as KIND, CURRENT_LIFE_AMOUNT_CAPABILITY as CAP)
from ..query_effect_amount_model import PublicQueryAmountError
from .live_modifier_quantities import current_modifier_quantity


_LIFE = CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_LIFE_TOTAL, schema_version=4)


def resolve_current_life_effect_amount(host, value, item):
    spec = CurrentLifeEffectAmountSpec.from_dict(value)
    controller = getattr(item, 'controller', None)
    if (type(controller) is not str or not controller or type(getattr(item, 'ref', None)) is not str
            or not item.ref or not isinstance(getattr(item, 'context', None), dict)):
        raise PublicQueryAmountError('Current life requires a locked controller and resolution context')
    cache = item.context.get('declared_current_life_amounts')
    if cache is not None and not isinstance(cache, dict):
        raise PublicQueryAmountError('Current-life continuation is malformed')
    if cache is not None and spec.binding_id in cache:
        previous = cache[spec.binding_id]
        if (not isinstance(previous, Mapping) or set(previous) != {'schema_version', 'controller', 'value'}
                or type(previous['schema_version']) is not int or previous['schema_version'] != 1
                or previous['controller'] != controller or type(previous['value']) is not int or previous['value'] < 0):
            raise PublicQueryAmountError('Current-life continuation changed its identity or value')
        return spec.coefficient * previous['value']
    amount = current_modifier_quantity(host, item, _LIFE)
    if cache is None:
        cache = {}
        item.context['declared_current_life_amounts'] = cache
    cache[spec.binding_id] = {'schema_version': 1, 'controller': controller, 'value': amount}
    return spec.coefficient * amount


def current_life_value_dependencies(value):
    if not isinstance(value, Mapping) or value.get('kind') != KIND:
        return ()
    CurrentLifeEffectAmountSpec.from_dict(value)
    return (CAP, 'quantity_expression.declared_effect_amount', 'quantity_expression.current_player_value')


def _contains_life(value):
    if isinstance(value, Mapping):
        return value.get('kind') == KIND or any(_contains_life(v) for v in value.values())
    return isinstance(value, (tuple, list)) and any(_contains_life(v) for v in value)


def _fixed_life_results(effects):
    from ..compiler.declared_effect_amounts import _result_slot
    from ..replacement.immutable import thaw_value
    bindings = set()

    def project(value, operation, path=()):
        if isinstance(value, Mapping):
            if value.get('kind') == KIND:
                if not _result_slot(operation, path):
                    raise ValueError('Current life is not a target or price declaration')
                spec = CurrentLifeEffectAmountSpec.from_dict(value); bindings.add(spec.binding_id)
                return spec.coefficient
            return {key: project(child, operation, (*path, key)) for key, child in value.items()}
        if isinstance(value, list):
            return [project(child, operation, path) for child in value]
        if isinstance(value, tuple):
            return tuple(project(child, operation, path) for child in value)
        return value

    projected = tuple(project(thaw_value(e), str(e.get('op') or '')) for e in effects)
    if len(bindings) != 1:
        raise ValueError('A current-life instruction requires one complete resolution binding')
    return projected


def _fixed_result_ownership(effects, target_schema):
    """Reconstruct reviewed leaf mechanics from data, then validate the leaf."""
    operations = tuple(effect.get('op') for effect in effects)
    if operations == ('create_token',) and target_schema is None:
        from .token_creation_capability_shapes import _fixed_token_effect_closure
        closure = _fixed_token_effect_closure(effects[0])
        if closure is not None:
            return closure
    if operations in {('place_counters',), ('place_counters', 'grant_keyword_until_end_of_turn')}:
        from .node_capability_shapes import fixed_counter_placement_node_capabilities, fixed_target_effect_sequence_node_capabilities
        from ..keyword_counters import keyword_counter_mechanic
        mechanics = {'cr-122-counters', 'cr-115-targets'}
        counter = keyword_counter_mechanic(effects[0].get('counter'))
        if counter is not None:
            mechanics.add(counter)
        owner = fixed_counter_placement_node_capabilities
        if len(effects) == 2:
            mechanics.update({'fixed-target-effect-sequence', 'cr-611-continuous-effects'})
            keyword = effects[1].get('keyword')
            if type(keyword) is str:
                mechanics.add(keyword.casefold())
            owner = fixed_target_effect_sequence_node_capabilities
        required = owner(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
        if required:
            return required, mechanics
    raise ValueError('Current life requires an independently closed token or counter result')


def current_life_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if not _contains_life((effects, target_schema, cost_schema)):
        return ()
    if _contains_life((target_schema, cost_schema)):
        raise ValueError('Current life cannot choose targets or pay source costs')
    projected = _fixed_life_results(effects)
    from .capabilities import capability_dependencies_for_node
    leaf, mechanics = _fixed_result_ownership(projected, target_schema)
    base = capability_dependencies_for_node(effects=projected, target_schema=target_schema,
        mechanic_ids=tuple(sorted(mechanics)), cost_schema=cost_schema)
    if not base:
        raise ValueError('Current-life result requires a complete existing fixed owner')
    return tuple(sorted({*current_life_value_dependencies(CurrentLifeEffectAmountSpec('shape').to_dict()), *leaf, *base}))


def current_life_program_dependencies(program):
    if not _contains_life((program.effects, program.handlers, program.target_schema, program.cost_schema)):
        return ()
    if not ((program.event == 'resolve' and program.active_zone == 'stack'
            and str(program.ability_id).startswith('spell:'))
            or (program.event == 'activate' and program.active_zone == 'battlefield')):
        raise ValueError('Current life requires an ordinary spell or battlefield activation')
    return current_life_node_capabilities(effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage, cost_schema=program.cost_schema)


def is_closed_current_life_program(program):
    try:
        required = current_life_program_dependencies(program)
    except (ValueError, TypeError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
