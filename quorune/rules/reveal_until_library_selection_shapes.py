from __future__ import annotations

from ..reveal_until_library_selection_model import (
    REVEAL_UNTIL_MECHANIC,reveal_until_selection_payload_dependencies,
)


def reveal_until_library_selection_node_capabilities(*,effects,target_schema,mechanic_ids):
    if REVEAL_UNTIL_MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    try:required = reveal_until_selection_payload_dependencies(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    return required if effects[0].get('player')=='$controller' else ()


def is_closed_reveal_until_library_selection_program(program):
    required = reveal_until_library_selection_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
