"""Fail-closed compiler ownership for the represented discard-result draws."""
from ..discard_result_model import DISCARD_RESULT_CAPABILITY, DISCARD_RESULT_MECHANIC, validate_discard_draw


def discard_result_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    if DISCARD_RESULT_MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1:return ()
    try:validate_discard_draw(effects[0])
    except (ValueError, TypeError):return ()
    effect = effects[0]
    if effect['actor'] != '$controller' or effect['players'] not in ('all', ['$controller']):return ()
    if effect['reduction'] == 'greatest' and effect['players'] != 'all':return ()
    return (DISCARD_RESULT_CAPABILITY,)


def is_closed_discard_result_program(program):
    deps = discard_result_node_capabilities(effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(deps) and set(deps) <= set(program.capability_dependencies)
