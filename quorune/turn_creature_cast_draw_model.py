"""Closed marker for a resolved, same-turn creature-cast draw subscription."""

from __future__ import annotations

from collections.abc import Mapping


TURN_CREATURE_CAST_DRAW_FIELD = "turn_creature_cast_draw"
TURN_CREATURE_CAST_DRAW_LABEL = "Draw a card when you cast a creature spell this turn"


def turn_creature_cast_draw_effect():
    return {
        "op": "delayed_trigger",
        "controller": "$controller",
        TURN_CREATURE_CAST_DRAW_FIELD: 1,
    }


def validate_turn_creature_cast_draw_effect(effect, *, actor="$controller"):
    expected = {"op", "controller", TURN_CREATURE_CAST_DRAW_FIELD}
    if actor != "$controller":
        expected.add("_runtime_source")
    if (
        not isinstance(effect, Mapping)
        or set(effect) != expected
        or effect.get("op") != "delayed_trigger"
        or effect.get("controller") != actor
        or type(effect[TURN_CREATURE_CAST_DRAW_FIELD]) is not int
        or effect[TURN_CREATURE_CAST_DRAW_FIELD] != 1
    ):
        raise ValueError("Same-turn creature-cast draw requires one closed resolving instruction")


def validate_turn_creature_cast_draw_trigger(trigger):
    condition = trigger.condition
    if (
        not isinstance(condition, Mapping)
        or set(condition) != {TURN_CREATURE_CAST_DRAW_FIELD}
        or type(condition[TURN_CREATURE_CAST_DRAW_FIELD]) is not int
        or condition[TURN_CREATURE_CAST_DRAW_FIELD] != 1
        or trigger.event_kind != "spell.cast"
        or trigger.label != TURN_CREATURE_CAST_DRAW_LABEL
        or type(trigger.controller) is not str
        or not trigger.controller
        or trigger.once is not False
        or type(trigger.active) is not bool
        or type(trigger.created_turn_sequence) is not int
        or trigger.created_turn_sequence < 0
        or type(trigger.expires_turn_sequence) is not int
        or trigger.expires_turn_sequence != trigger.created_turn_sequence
        or trigger.stack_template != {}
        or trigger.source_object_id is not None
        or trigger.source_logical_object_id is not None
        or trigger.referred_object_ids
    ):
        raise ValueError("Same-turn creature-cast draw requires its exact repeated subscription")
