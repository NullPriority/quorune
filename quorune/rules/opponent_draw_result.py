"""Closed spell that counts distinct committed drawing opponents."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import thaw_value
from .effect_shape_walkers import contains_nested_key, nested_effects


OPPONENT_DRAW_RESULT_FIELD = "opponent_draw_result"
OPPONENT_DRAW_CAPABILITY = "zone.draw.distinct_opponents_committed_followup"
OPPONENT_DRAW_MECHANIC = "distinct-opponent-draw-result"
OPPONENT_DRAW_OBSERVER_FIELD = "opponent_draw_observer"
OPPONENT_DRAW_OBSERVER_ACTION = "observe_opponent_draw"
OPPONENT_DRAW_RECEIPT_FIELD = "_opponent_draw_receipt"
DEPENDENCIES = (OPPONENT_DRAW_CAPABILITY, "zone.draw.library_to_hand")


@dataclass(frozen=True, slots=True)
class ObserveOpponentDraw:
    """Bind an actual drawing opponent to its originating stack resolution."""

    stack_ref: str
    controller: str

    def __post_init__(self):
        if any(type(value) is not str or not value or value.startswith("$")
               for value in (self.stack_ref, self.controller)):
            raise ValueError("An opponent draw observer requires its resolving stack and controller")

    def to_dict(self):
        return {"action": OPPONENT_DRAW_OBSERVER_ACTION, "stack_ref": self.stack_ref,
                "controller": self.controller}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"action", "stack_ref", "controller"}
                or value["action"] != OPPONENT_DRAW_OBSERVER_ACTION):
            raise ValueError("An opponent draw observer requires its exact typed fields")
        return cls(value["stack_ref"], value["controller"])


def opponent_draw_effects():
    return (
        {"op": "draw_each_player", "schema_version": 1, OPPONENT_DRAW_RESULT_FIELD: "observe"},
        {"op": "draw", "schema_version": 1, OPPONENT_DRAW_RESULT_FIELD: "count"},
    )


def opponent_draw_dependencies(program):
    claimed = OPPONENT_DRAW_CAPABILITY in program.capability_dependencies
    marked = any(contains_nested_key(program.effects, key) for key in (
        OPPONENT_DRAW_RESULT_FIELD, OPPONENT_DRAW_OBSERVER_FIELD,
    ))
    serialized_observer = any(
        isinstance(action, Mapping) and action.get("action") == OPPONENT_DRAW_OBSERVER_ACTION
        for effect in nested_effects(program.effects)
        for actions in (effect.get("post_draw_actions"),)
        if isinstance(actions, (list, tuple))
        for action in actions
    )
    if not (claimed or marked or serialized_observer):
        return ()
    effects = tuple(thaw_value(value) for value in program.effects)
    if (
        effects != opponent_draw_effects()
        or any(type(effect.get("schema_version")) is not int for effect in effects)
        or (program.event, program.active_zone) != ("resolve", "stack")
        or program.target_schema is not None or program.cost_schema is not None
        or program.event_condition is not None or program.handlers
    ):
        raise ValueError("The distinct opponent draw result requires its whole untargeted spell")
    return DEPENDENCIES


def is_closed_opponent_draw_program(program):
    try:
        dependencies = opponent_draw_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(dependencies) and set(dependencies) <= set(program.capability_dependencies)
