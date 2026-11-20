"""Bind chosen kinds to the existing target and canonical counter-removal owners."""
from collections.abc import Mapping
from ..chosen_counter_removal_model import (
    ChosenCounterRemovalSpec, CHOSEN_COUNTER_REMOVAL_OPERATION,
    CHOSEN_COUNTER_REMOVAL_CAPABILITY, CHOSEN_COUNTER_REMOVAL_MECHANIC,
)


def chosen_counter_removal_payload_dependencies(effect):
    ChosenCounterRemovalSpec.decode(effect)
    return (CHOSEN_COUNTER_REMOVAL_CAPABILITY, 'counter.removal.fixed_effect', 'target.revalidate_resolution')


def _target_schema_is_closed(schema):
    from ..compiler.direct_target import direct_permanent_target_spec
    return (isinstance(schema, Mapping) and type(schema.get('count')) is int
            and any(dict(schema) == direct_permanent_target_spec(subject).to_target_schema()
                    for subject in ('target permanent', 'target nonland permanent')))


def chosen_counter_removal_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or CHOSEN_COUNTER_REMOVAL_MECHANIC not in mechanic_ids
            or not _target_schema_is_closed(target_schema)):
        return ()
    try:return chosen_counter_removal_payload_dependencies(effects[0])
    except (TypeError, ValueError):return ()


def is_closed_chosen_counter_removal_program(program):
    required = chosen_counter_removal_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)


def chosen_counter_removal_runtime_dependencies(program):
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if value.get('op') == CHOSEN_COUNTER_REMOVAL_OPERATION:
                required.update(chosen_counter_removal_payload_dependencies(value))
                if not _target_schema_is_closed(program.target_schema):
                    raise ValueError('Chosen counter removal requires one complete permanent target')
            for child in value.values():visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))
