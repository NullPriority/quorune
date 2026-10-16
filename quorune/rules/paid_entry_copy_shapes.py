from __future__ import annotations

"""Close only the paid source-copy entry grammar, never an unconditional loop."""

from collections.abc import Mapping
from ..paid_entry_copy_model import (PAID_ENTRY_COPY_CAPABILITY, PAID_ENTRY_COPY_MECHANIC,
    SQUAD_ENTRY_COPY_CAPABILITY, SQUAD_ENTRY_COPY_MECHANIC, SQUAD_ENTRY_COPY_QUANTITY)
from ..token_copy_recipes import TokenCopyExceptionSpec, TokenCopyRecipeSpec


def paid_entry_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len({PAID_ENTRY_COPY_MECHANIC, SQUAD_ENTRY_COPY_MECHANIC} & set(mechanic_ids)) != 1:
        return ()
    squad = SQUAD_ENTRY_COPY_MECHANIC in mechanic_ids
    if not ({PAID_ENTRY_COPY_MECHANIC, SQUAD_ENTRY_COPY_MECHANIC} & set(mechanic_ids)) or target_schema is not None or len(effects) != 1:
        return ()
    expected = {'op': 'create_token', 'controller': '$controller', 'quantity': SQUAD_ENTRY_COPY_QUANTITY if squad else 1,
        'copy_spec': TokenCopyRecipeSpec(origin='source', exception=TokenCopyExceptionSpec() if squad else TokenCopyExceptionSpec(power=1, toughness=1)).to_dict()}
    if effects[0] != expected or not squad and type(effects[0].get('quantity')) is not int:
        return ()
    return (SQUAD_ENTRY_COPY_CAPABILITY if squad else PAID_ENTRY_COPY_CAPABILITY, 'token.creation.fixed_copy',
        'trigger.event.normalized_zone_change', 'trigger.placement.apnap')


def is_closed_paid_entry_copy_program(program):
    required = paid_entry_copy_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    condition = program.event_condition
    return bool(required and program.event == 'permanent.enter.self'
        and isinstance(condition, Mapping) and set(condition) == {'field', 'op', 'value'}
        and condition['field'] == 'paid_entry_copy_instance' and condition['op'] == 'eq'
        and type(condition['value']) is str and condition['value']
        and set(required).issubset(program.capability_dependencies))
