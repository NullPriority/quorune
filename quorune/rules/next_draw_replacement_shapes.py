"""Bind scheduled draw data to the existing ordinary result owners."""

from ..created_token_counter_model import validate_single_created_token
from ..next_draw_replacement_model import (
    NEXT_DRAW_REPLACEMENT_CAPABILITY, NEXT_DRAW_REPLACEMENT_MECHANIC,
    NEXT_DRAW_REPLACEMENT_OPERATION, PUBLIC_PERMANENT_RETURN_RESULT, NextDrawResult,
)

PUBLIC_PERMANENT_RETURN_CAPABILITY = "zone.draw.next_controller_public_return"


def require_scheduled_draw_result_ownership(result: NextDrawResult) -> None:
    if result.kind == "create_token":
        validate_single_created_token({**result.payload, "controller": "$controller"})


def next_draw_replacement_node_capabilities(*, effects, target_schema, mechanic_ids, **_):
    if (
        NEXT_DRAW_REPLACEMENT_MECHANIC not in mechanic_ids
        or len(effects) != 1
        or target_schema is not None
    ):
        return ()
    try:
        result = NextDrawResult.from_instruction(effects[0])
        require_scheduled_draw_result_ownership(result)
    except (TypeError, ValueError):
        return ()
    dependencies = {
        "gain_life": ("life.change.effect",),
        "create_token": ("token.creation.fixed_definition",),
        "discard_each_opponent": ("zone.change.destination_replacement", "zone.discard.typed_cause"),
        PUBLIC_PERMANENT_RETURN_RESULT: (PUBLIC_PERMANENT_RETURN_CAPABILITY, "zone.change.destination_replacement"),
    }
    return NEXT_DRAW_REPLACEMENT_CAPABILITY, *dependencies[result.kind]


def next_draw_replacement_runtime_dependencies(program):
    if not any(effect.get("op") == NEXT_DRAW_REPLACEMENT_OPERATION for effect in program.effects):
        return ()
    required = next_draw_replacement_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (
        not required
        or (program.event, program.active_zone) != ("activate", "battlefield")
        or len(program.handlers) != 1
        or program.handlers[0].get("handler_id") != "activation.catalog.pinned.v1"
    ):
        raise ValueError("A controller next-draw result needs its one closed battlefield activation")
    return required
