"""The closed second-cast copy and linked original spell suspension."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..event_spell_copy_model import (
    EVENT_SPELL_COPY_CAPABILITY, EVENT_SPELL_COPY_CONTEXT, EVENT_SPELL_COPY_MECHANIC,
    event_spell_copy_effect,
)
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


CAPABILITY = "stack.copy.second_cast_suspend_original"
MECHANIC = "second-cast-copy-and-suspend"
TEMPLATE = "second-cast-copy-and-suspend-v1"
FIELD = "cast_spell_suspend_version"
EXILE_MARKER = "cast_suspended_exile"
TIME_COUNTERS = 4
REQUIRED = (
    CAPABILITY, EVENT_SPELL_COPY_CAPABILITY, "trigger.event.normalized_spell_cast",
    "trigger.effect.fixed_event", "trigger.placement.apnap", "zone.change.destination_replacement",
    "token.creation.defined_transforming", "casting.permission.one_shot_exile_without_mana",
    "casting.lifecycle.fixed_zone_cast", "counter.placement.quantity_replacement",
)


def second_controller_cast_condition():
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "caster_spell_number", "op": "eq", "value": 2},
    ]}


def cast_copy_then_suspend_effects():
    return (
        event_spell_copy_effect(retain_targets_only=True),
        {"op": "exile", FIELD: 1, "stack": "$context.stack",
         "copy_source": "$context." + EVENT_SPELL_COPY_CONTEXT},
    )


def cast_spell_suspend_node_capabilities(*, effects, target_schema, mechanics):
    if MECHANIC not in mechanics and not any(FIELD in effect for effect in nested_effects(effects)):
        return None
    if (not {MECHANIC, EVENT_SPELL_COPY_MECHANIC} <= set(mechanics) or target_schema is not None
            or len(effects) != 2 or type(effects[1].get(FIELD)) is not int
            or thaw_value(tuple(effects)) != list(cast_copy_then_suspend_effects())):
        return ()
    return REQUIRED


def cast_spell_suspend_runtime_dependencies(program):
    required = cast_spell_suspend_node_capabilities(
        effects=(*program.effects, *program.handlers), target_schema=program.target_schema,
        mechanics=program.coverage,
    )
    if required is None:
        return None
    condition = thaw_value(program.event_condition)
    if (not required or program.event != "spell.cast" or program.active_zone != "battlefield"
            or condition != second_controller_cast_condition() or type(condition["all"][1]["value"]) is not int
            or program.cost_schema is not None or program.handlers):
        raise ValueError("Cast-copy suspension requires its exact second-cast triggered result")
    return required


def is_closed_cast_spell_suspend_program(program):
    try:
        required = cast_spell_suspend_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


@dataclass(frozen=True, slots=True)
class CastSuspendedExile:
    logical_object_id: str
    grants_suspend: bool
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.logical_object_id) is not str or not self.logical_object_id
                or type(self.grants_suspend) is not bool
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError("Cast suspension requires one exact exile incarnation")

    def to_dict(self):
        return {"schema_version": self.schema_version, "logical_object_id": self.logical_object_id,
                "grants_suspend": self.grants_suspend}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "logical_object_id", "grants_suspend"}:
            raise ValueError("Cast suspension grant fields are closed")
        return cls(**value)


def current_cast_suspended_exile(card):
    if card is None or card.zone != "exile" or card.object_kind != "card":
        return None
    try:
        marker = CastSuspendedExile.from_dict(card.annotations.get(EXILE_MARKER))
    except (TypeError, ValueError):
        return None
    return marker if marker.logical_object_id == card.logical_object_id else None
