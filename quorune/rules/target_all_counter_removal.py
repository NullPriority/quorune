"""Close target cardinality and kind without changing counter mutation ownership."""
from collections.abc import Mapping
from ..counter_names import normalized_counter_name

NAMED_TARGET_ALL_COUNTER_CAPABILITY = 'counter.removal.all_named_target_effect'
OPTIONAL_TARGET_ALL_COUNTER_CAPABILITY = 'counter.removal.optional_target_all_effect'
_NAMED_MECHANIC = 'all-named-target-counter-removal'
_OPTIONAL_MECHANIC = 'optional-target-all-counter-removal'


def named_target_all_counter_payload_dependencies(effect):
    if (not isinstance(effect, Mapping)
            or set(effect) != {'op', 'schema_version', 'card', 'counter', 'source'}
            or effect.get('op') != 'remove_all_counters'
            or type(effect.get('schema_version')) is not int or effect['schema_version'] != 3
            or effect.get('card') != '$target.0' or effect.get('source') != '$source'
            or type(effect.get('counter')) is not str or not effect['counter']
            or normalized_counter_name(effect['counter']) != effect['counter']):
        raise ValueError('Named target all-counter removal requires its complete version-three instruction')
    return (NAMED_TARGET_ALL_COUNTER_CAPABILITY, 'counter.removal.all_effect', 'target.revalidate_resolution')


def target_all_counter_variant_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or not isinstance(target_schema, Mapping)
            or not {'cr-122-counters', 'cr-115-targets'} <= set(mechanic_ids)):
        return ()
    from .node_capability_shapes import fixed_counter_target_schema_is_closed, direct_target_predicate_capabilities
    schema = dict(target_schema)
    optional = 'up_to' in schema
    if optional:
        if type(schema.get('up_to')) is not int or schema['up_to'] != 1 or 'count' in schema:return ()
        schema.pop('up_to')
        schema['count'] = 1
    if not fixed_counter_target_schema_is_closed(schema):return ()
    effect, = effects
    if effect.get('schema_version') == 3:
        if optional or _NAMED_MECHANIC not in mechanic_ids:return ()
        try:required = named_target_all_counter_payload_dependencies(effect)
        except (TypeError, ValueError):return ()
    elif (optional and _OPTIONAL_MECHANIC in mechanic_ids
            and effect == {'op': 'remove_all_counters', 'card': '$target.0', 'source': '$source'}):
        required = (OPTIONAL_TARGET_ALL_COUNTER_CAPABILITY, 'counter.removal.all_effect', 'target.revalidate_resolution')
    else:return ()
    return tuple(sorted({*required, *direct_target_predicate_capabilities(schema)}))


def target_all_counter_variant_runtime_dependencies(program):
    from ..counter_removal import ALL_PERMANENT_COUNTERS_THEN_TOKEN_EXILE_FIELD
    from .counter_removal_capabilities import all_permanent_counters_then_token_exile_dependencies
    from .effect_shape_walkers import nested_effects

    if any(ALL_PERMANENT_COUNTERS_THEN_TOKEN_EXILE_FIELD in effect
           for effect in nested_effects((program.effects, program.handlers))):
        needed = all_permanent_counters_then_token_exile_dependencies(
            program.effects, program.target_schema, program.coverage,
        )
        if (not needed or (program.event, program.active_zone) != ("resolve", "stack")
                or program.event_condition is not None or program.cost_schema is not None or program.handlers):
            raise ValueError("The global counter and token result requires the exact untargeted resolving spell")
        return needed
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if value.get('op') == 'remove_all_counters':
                schema = program.target_schema
                if (value.get('card') == '$target.0' and ('counter' in value or 'schema_version' in value)
                        and (type(value.get('schema_version')) is not int or value['schema_version'] != 3)):
                    raise ValueError('Named target removal cannot borrow the legacy source codec')
                named = value.get('schema_version') == 3
                optional = isinstance(schema, Mapping) and 'up_to' in schema and value.get('card') == '$target.0'
                if named or optional:
                    mechanics = ('cr-122-counters', 'cr-115-targets', _NAMED_MECHANIC if named else _OPTIONAL_MECHANIC)
                    deps = target_all_counter_variant_capabilities(effects=(value,), target_schema=schema,
                        mechanic_ids=mechanics)
                    if not deps:raise ValueError('Target all-counter variant lacks its complete target declaration')
                    required.update(deps)
            for child in value.values():visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))


def is_closed_target_all_counter_variant_program(program):
    deps = target_all_counter_variant_capabilities(effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=program.coverage)
    return bool(deps) and set(deps) <= set(program.capability_dependencies)
