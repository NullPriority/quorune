from __future__ import annotations

"""Own one optional controller draw followed by a private drawn-card settlement."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.immutable import freeze_value, thaw_value
from .effect_shape_walkers import contains_nested_key

DRAW_STEP_SETTLEMENT_MECHANIC = "draw-step-drawn-card-settlement"
DRAW_STEP_SETTLEMENT_CAPABILITY = "library.draw.current_turn_settlement"
DRAW_STEP_SETTLEMENT_VERSION_FIELD = "draw_step_settlement_version"

_DRAW_COUNT = 2
_RESULT_DEPENDENCIES = (
    DRAW_STEP_SETTLEMENT_CAPABILITY,
    "zone.draw.library_to_hand",
    "zone.change.destination_replacement",
    "library.order.fixed_hand_putback",
)
_TRIGGER_DEPENDENCIES = (
    *_RESULT_DEPENDENCIES,
    "trigger.effect.fixed_event",
    "trigger.placement.apnap",
)


def controller_draw_step_condition():
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "draw"},
    ]}


def draw_step_settlement_effect(*, player="$controller"):
    return {
        "op": "choose_option",
        "player": player,
        DRAW_STEP_SETTLEMENT_VERSION_FIELD: 1,
        "prompt": "Draw two additional cards with Sylvan Library?",
        "options": [
            {"id": "draw", "label": "Draw two additional cards"},
            {"id": "decline", "label": "Do not draw"},
        ],
        "then_by_choice": {
            "draw": [
                {"op": "draw", "player": player, "count": _DRAW_COUNT, "private": True},
                {"op": "sylvan_library_settle", "player": player, DRAW_STEP_SETTLEMENT_VERSION_FIELD: 1},
            ],
            "decline": [],
        },
    }


def validate_draw_step_settlement_effect(effect, *, player="$controller"):
    candidate = thaw_value(freeze_value(effect))
    if (
        candidate != draw_step_settlement_effect(player=player)
        or type(candidate[DRAW_STEP_SETTLEMENT_VERSION_FIELD]) is not int
        or type(candidate["then_by_choice"]["draw"][0]["count"]) is not int
        or type(candidate["then_by_choice"]["draw"][1][DRAW_STEP_SETTLEMENT_VERSION_FIELD]) is not int
        or candidate["then_by_choice"]["draw"][0]["private"] is not True
    ):
        raise ValueError("Draw-step settlement requires its exact private draw and settlement branches")


def draw_step_settlement_can_draw(effect, *, actor, controller, query):
    if actor != controller or actor not in query.active_seats:
        raise ValueError("The resolving trigger controller must choose the draw-step settlement")
    validate_draw_step_settlement_effect(effect, player=actor)
    return (query.drawn_hand_incarnations_this_turn(actor) is not None
            and query.draw_permission(actor).allows_complete_draw(_DRAW_COUNT))


def draw_step_settlement_node_capabilities(*, effects, target_schema, cost_schema=None):
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    try:
        validate_draw_step_settlement_effect(effects[0])
    except (TypeError, ValueError):
        return ()
    return _RESULT_DEPENDENCIES


def draw_step_settlement_program_dependencies(program):
    if (
        DRAW_STEP_SETTLEMENT_CAPABILITY not in program.capability_dependencies
        and DRAW_STEP_SETTLEMENT_MECHANIC not in program.coverage
        and not contains_nested_key((program.effects, program.handlers), DRAW_STEP_SETTLEMENT_VERSION_FIELD)
    ):
        return ()
    if (
        DRAW_STEP_SETTLEMENT_MECHANIC not in program.coverage
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or (program.event, program.active_zone) != ("step.begin", "battlefield")
        or not program.ability_id.startswith("trigger:")
        or program.destination is not None
        or program.requires_arbiter
        or program.handlers
        or program.trigger_limit is not None
        or thaw_value(freeze_value(program.event_condition)) != controller_draw_step_condition()
        or not draw_step_settlement_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, cost_schema=program.cost_schema,
        )
    ):
        raise ValueError("Draw-step settlement requires the exact controller trigger and option branches")
    return _TRIGGER_DEPENDENCIES


def is_closed_draw_step_settlement_program(program):
    try:
        required = draw_step_settlement_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required and set(required) <= set(program.capability_dependencies))


def draw_step_settlement_supersedes_reviewed(current, reviewed):
    if not is_closed_draw_step_settlement_program(current) or reviewed.capability_dependencies:
        return False
    previous_effect = draw_step_settlement_effect()
    previous_effect.pop(DRAW_STEP_SETTLEMENT_VERSION_FIELD)
    previous_effect["then_by_choice"]["draw"][1].pop(DRAW_STEP_SETTLEMENT_VERSION_FIELD)
    return thaw_value(freeze_value(reviewed.effects)) == [previous_effect]
