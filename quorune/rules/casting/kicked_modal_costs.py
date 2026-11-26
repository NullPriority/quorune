"""Correlate each offered mode schema with its already validated Kicker payment."""

from ...kicker import KICKER_MECHANIC_ID
from ...kicked_modal import KICKED_MODAL_CAPABILITY, KICKED_MODAL_FIELD, KickedModalSpec


def with_kicked_modal_mode_cost_options(program, schema, options):
    if KICKED_MODAL_FIELD not in schema:
        return options
    try:
        if (program is None or KICKED_MODAL_CAPABILITY not in program.capability_dependencies
                or schema[KICKED_MODAL_FIELD] != (program.cost_schema or {}).get(KICKED_MODAL_FIELD)):
            return []
        policy = KickedModalSpec.from_dict(schema[KICKED_MODAL_FIELD])
        kicked_schema = policy.kicked_schema(program.target_schema)
    except (TypeError, ValueError):
        return []
    return [
        {**option, "target_schema": kicked_schema}
        if option.get("kind") == KICKER_MECHANIC_ID and option.get("kicker_fingerprint")
        else option
        for option in options
    ]
