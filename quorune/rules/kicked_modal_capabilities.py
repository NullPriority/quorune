"""Close the price-dependent mode cardinality independently of its declared owners."""

from collections.abc import Mapping

from ..kicker import KICKER_CAPABILITY_ID
from ..kicked_modal import KICKED_MODAL_CAPABILITY, KICKED_MODAL_FIELD, KickedModalSpec


def kicked_modal_capabilities(*, effects, target_schema, mechanics, cost_schema, modal_resolver):
    if (not isinstance(cost_schema, Mapping) or set(cost_schema) != {KICKED_MODAL_FIELD}
            or effects or "fixed-nonrepeating-modal" not in mechanics):
        return ()
    try:
        spec = KickedModalSpec.from_dict(cost_schema[KICKED_MODAL_FIELD])
        spec.validate_schema(target_schema)
    except (TypeError, ValueError):
        return ()
    owned = modal_resolver(effects=effects, target_schema=target_schema, mechanics=mechanics)
    return (*owned, KICKER_CAPABILITY_ID, KICKED_MODAL_CAPABILITY) if owned else ()


def kicked_modal_runtime_dependencies(program):
    schema = program.cost_schema or {}
    if KICKED_MODAL_FIELD not in schema and KICKED_MODAL_CAPABILITY not in program.capability_dependencies:
        return ()
    if (set(schema) != {KICKED_MODAL_FIELD} or program.effects
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.handlers or program.event_condition is not None):
        raise ValueError("Kicked-modal options require their complete resolving spell")
    policy = KickedModalSpec.from_dict(schema[KICKED_MODAL_FIELD])
    policy.validate_schema(program.target_schema)
    return (KICKER_CAPABILITY_ID, KICKED_MODAL_CAPABILITY)
