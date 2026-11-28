"""The typed two-instruction follow-up for opponents who actually drew."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .effect_shape_walkers import contains_nested_key


DISTINCT_DRAW_CAPABILITY = "zone.draw.distinct_opponent_followup"
DISTINCT_DRAW_PHASE = "distinct_opponent_draw_phase"
DISTINCT_DRAW_ACTION = "count_distinct_opponent_draw"
DISTINCT_DRAW_OBSERVER = "distinct_opponent_draw_observer"
DISTINCT_DRAW_RECEIPT = "_distinct_opponent_draw_receipt"
_REQUIRED = ("zone.draw.library_to_hand", "zone.draw.result_generated_ordering", DISTINCT_DRAW_CAPABILITY)


@dataclass(frozen=True, slots=True)
class CountDistinctOpponentDraw:
    """Observe a committed draw descended from one spell's opponent batch."""

    stack_ref: str
    controller: str

    def __post_init__(self):
        for value in (self.stack_ref, self.controller):
            if type(value) is not str or not value or value.startswith("$"):
                raise ValueError("An opponent draw observer requires a resolved stack and controller")

    def to_dict(self):
        return {"action": DISTINCT_DRAW_ACTION, "stack_ref": self.stack_ref, "controller": self.controller}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"action", "stack_ref", "controller"}
                or value["action"] != DISTINCT_DRAW_ACTION):
            raise ValueError("An opponent draw observer requires exact typed fields")
        return cls(value["stack_ref"], value["controller"])


@dataclass(frozen=True, slots=True)
class DistinctOpponentDrawReceipt:
    """Record only public seats that drew through the observed instruction."""

    observer: CountDistinctOpponentDraw
    opponents: tuple[str, ...]
    drawers: tuple[str, ...] = ()

    def __post_init__(self):
        for seats in (self.opponents, self.drawers):
            if (not isinstance(seats, tuple) or any(type(seat) is not str or not seat for seat in seats)
                    or seats != tuple(sorted(set(seats))) or self.observer.controller in seats):
                raise ValueError("The opponent draw receipt requires sorted distinct opposing seats")
        if not set(self.drawers).issubset(self.opponents):
            raise ValueError("The draw receipt contains a drawer outside the original opponents")

    def with_drawer(self, seat):
        if seat not in self.opponents or seat in self.drawers:
            return self
        return replace(self, drawers=tuple(sorted((*self.drawers, seat))))

    def to_dict(self):
        return {**self.observer.to_dict(), "schema_version": 1,
                "opponents": list(self.opponents), "drawers": list(self.drawers)}

    @classmethod
    def from_dict(cls, value, observer):
        identity = {**observer.to_dict(), "schema_version": 1}
        if (not isinstance(value, Mapping) or set(value) != {*identity, "opponents", "drawers"}
                or type(value["schema_version"]) is not int
                or any(value[key] != expected for key, expected in identity.items())
                or not isinstance(value["opponents"], list) or not isinstance(value["drawers"], list)):
            raise ValueError("The receipt must match its exact opponent draw observer")
        return cls(observer, tuple(value["opponents"]), tuple(value["drawers"]))


def distinct_opponent_draw_effects():
    return (
        {"op": "draw_each_player", "schema_version": 1, DISTINCT_DRAW_PHASE: "opponents"},
        {"op": "draw", "schema_version": 1, DISTINCT_DRAW_PHASE: "controller"},
    )


def distinct_draw_node_capabilities(effects, target_schema):
    if (target_schema is None and tuple(effects) == distinct_opponent_draw_effects()
            and all(type(effect["schema_version"]) is int for effect in effects)):
        return _REQUIRED
    return ()


def distinct_draw_runtime_dependencies(program):
    if (DISTINCT_DRAW_CAPABILITY not in program.capability_dependencies
            and not contains_nested_key((program.effects, program.handlers), DISTINCT_DRAW_PHASE)):
        return ()
    required = distinct_draw_node_capabilities(program.effects, program.target_schema)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.cost_schema is not None
            or program.event_condition is not None or program.handlers):
        raise ValueError("An opponent draw follow-up requires its complete untargeted spell")
    return required
