from __future__ import annotations

"""Bind a kicked entry's one public artifact target and following attachment."""

from collections.abc import Mapping
from dataclasses import replace

from ..entry_x_amount_model import (
    ENTRY_X_AMOUNT_CAPABILITY, EntryXAmountSpec, resolve_entry_x_for_occurrence,
)
from ..kicker import POSITIVE_GENERIC_KICKER_CAPABILITY
from ..replacement.immutable import thaw_value
from .attachment_actions import (
    TARGETED_EQUIPMENT_CAPABILITY, targeted_equipment_attachment_effect,
    validate_targeted_equipment_attachment,
)


KICKED_ARTIFACT_ENTRY_CAPABILITY = "trigger.entry.kicked_artifact_control"
KICKED_ARTIFACT_ENTRY_LIMIT = "kicked_entry_artifact_mana_value_limit"
KICKED_ARTIFACT_ATTACH = "kicked_entry_artifact_attachment"
KICKED_ARTIFACT_ENTRY_REQUIREMENTS = (
    KICKED_ARTIFACT_ENTRY_CAPABILITY, POSITIVE_GENERIC_KICKER_CAPABILITY,
    ENTRY_X_AMOUNT_CAPABILITY, TARGETED_EQUIPMENT_CAPABILITY,
)


def kicked_artifact_entry_condition():
    return {"field": "cast_option", "op": "eq", "value": "kicked"}


def kicked_artifact_entry_schema():
    return {
        "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
        "types_any": ["artifact"], KICKED_ARTIFACT_ENTRY_LIMIT: EntryXAmountSpec().to_dict(),
    }


def kicked_artifact_entry_effects():
    return (
        {"op": "gain_control", "card": "$target.0", "controller": "$controller",
         "duration": "zone_object", "source": "$source"},
        {**targeted_equipment_attachment_effect("$source.zone_object"), KICKED_ARTIFACT_ATTACH: 1},
    )


def has_kicked_artifact_entry_limit(schema):
    return isinstance(schema, Mapping) and KICKED_ARTIFACT_ENTRY_LIMIT in schema


def bind_kicked_artifact_entry_target(schema, bound):
    target = dict(thaw_value(schema)) if isinstance(schema, Mapping) else None
    if (target != kicked_artifact_entry_schema() or type(target.get("count")) is not int
            or EntryXAmountSpec.from_dict(target[KICKED_ARTIFACT_ENTRY_LIMIT]) != EntryXAmountSpec()
            or type(bound) is not int or bound < 1):
        raise ValueError("Kicked entry requires its exact single artifact target and positive paid bound")
    del target[KICKED_ARTIFACT_ENTRY_LIMIT]
    return {**target, "mana_value_max": bound}


def _project_kicked_artifact_entry(effects, schema, mechanics):
    from .control_capability_shapes import fixed_control_node_capabilities

    target = bind_kicked_artifact_entry_target(schema, 1)
    if (tuple(thaw_value(value) for value in effects) != kicked_artifact_entry_effects()
            or type(effects[1][KICKED_ARTIFACT_ATTACH]) is not int):
        raise ValueError("Kicked entry requires persistent control followed by its own Equipment attachment")
    validate_targeted_equipment_attachment({
        key: value for key, value in effects[1].items() if key != KICKED_ARTIFACT_ATTACH
    })
    fixed = fixed_control_node_capabilities(effects=effects[:1], target_schema=target, mechanic_ids=mechanics)
    if not fixed:
        raise ValueError("Kicked entry requires the existing public control owner")
    return target, tuple(sorted({*KICKED_ARTIFACT_ENTRY_REQUIREMENTS, *fixed, "target.revalidate_resolution"}))


def kicked_artifact_entry_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    if cost_schema is not None:
        return ()
    try:
        return _project_kicked_artifact_entry(effects, target_schema, mechanics)[1]
    except (TypeError, ValueError, KeyError):
        return ()


def project_kicked_artifact_entry_program(program):
    if (
        (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
        or thaw_value(program.event_condition) != kicked_artifact_entry_condition()
        or program.cost_schema is not None or program.handlers
    ):
        raise ValueError("The artifact result requires its original kicked self-entry trigger")
    target, required = _project_kicked_artifact_entry(program.effects, program.target_schema, program.coverage)
    return replace(program, target_schema=target, effects=program.effects[:1]), required


def is_closed_kicked_artifact_entry_program(program, *, body_is_closed):
    try:
        projected, required = project_kicked_artifact_entry_program(program)
    except (TypeError, ValueError, KeyError):
        return False
    return set(required).issubset(program.capability_dependencies) and body_is_closed(projected)


def kicked_artifact_entry_trigger_context(program, source, context):
    schema = program.target_schema
    if not has_kicked_artifact_entry_limit(schema):
        return {}
    bound = resolve_entry_x_for_occurrence(
        schema[KICKED_ARTIFACT_ENTRY_LIMIT], source_object_id=source.object_id, context=context,
    )
    return {"target_schema_override": bind_kicked_artifact_entry_target(schema, bound)}


def checked_kicked_artifact_attachment(host, effect, *, actor):
    if type(effect.get(KICKED_ARTIFACT_ATTACH)) is not int or effect[KICKED_ARTIFACT_ATTACH] != 1:
        raise ValueError("Kicked entry Equipment attachment requires its versioned instruction")
    attachment = {key: value for key, value in effect.items() if key != KICKED_ARTIFACT_ATTACH}
    equipment_ref, entrant_ref = validate_targeted_equipment_attachment(attachment, resolved=True)
    if equipment_ref is None or entrant_ref is None:
        return None, None
    equipment = host._resolve_object(actor, equipment_ref, zones={"battlefield"})
    _, subtypes, _ = host._type_parts(str(host._effective_card_data(equipment).get("type_line") or ""))
    return (equipment_ref, entrant_ref) if "equipment" in subtypes else (None, None)
