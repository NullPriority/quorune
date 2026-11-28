"""Cast facts and exact rules for one mana-value-bounded revealed-library cast."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from .spell_cast_events import SpellCastEvent


MANA_VALUE_CAST_FIELD = "revealed_library_cast_by_mana_value"
MANA_VALUE_CAST_CONTEXT = "hand_cast_reveal_snapshot"
CAPABILITY = "library.reveal.cast_one_at_committed_mana_value"
DEPENDENCIES = (CAPABILITY, "trigger.effect.fixed_event",
                "trigger.event.normalized_spell_cast", "trigger.placement.apnap")
HAND_CAST_CONDITION = FrozenMap({"all": [
    {"field": "controller", "op": "eq", "value": "$source.controller"},
    {"field": "from", "op": "eq", "value": "hand"},
]})


@dataclass(frozen=True, slots=True)
class HandCastRevealSnapshot:
    controller: str
    source_spell_ref: str
    logical_object_id: str
    mana_value: int

    def __post_init__(self):
        if (any(type(value) is not str or not value or value.startswith("$") for value in (
                self.controller, self.source_spell_ref, self.logical_object_id))
                or type(self.mana_value) is not int or self.mana_value < 0):
            raise ValueError("The reveal requires the original from-hand cast and its mana value")

    def to_dict(self):
        return {"controller": self.controller, "source_spell_ref": self.source_spell_ref,
                "logical_object_id": self.logical_object_id, "mana_value": self.mana_value}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
            "controller", "source_spell_ref", "logical_object_id", "mana_value",
        }:
            raise ValueError("The from-hand cast snapshot has an unsupported shape")
        return cls(**value)


def capture_hand_cast_reveal_snapshot(context, controller):
    cast = SpellCastEvent.from_context(context)
    if (cast.origin != "hand" or cast.controller != controller or cast.mana_value is None
            or not cast.mana_value.is_integer()):
        raise ValueError("The reveal requires an actual spell cast from the source controller's hand")
    return HandCastRevealSnapshot(cast.controller, cast.stack_ref, cast.logical_object_id, int(cast.mana_value))


def mana_value_cast_instruction(*, actor="$controller", snapshot=None):
    return {"op": "choose_objects", "player": actor, MANA_VALUE_CAST_FIELD: 1,
            "cast_snapshot": "$context." + MANA_VALUE_CAST_CONTEXT if snapshot is None else snapshot.to_dict()}


def decode_mana_value_cast_instruction(effect, *, actor, allow_symbolic):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", MANA_VALUE_CAST_FIELD, "cast_snapshot"}
            or type(effect[MANA_VALUE_CAST_FIELD]) is not int or effect[MANA_VALUE_CAST_FIELD] != 1):
        raise ValueError("The revealed cast must use its exact version")
    snapshot = None if allow_symbolic else HandCastRevealSnapshot.from_dict(effect["cast_snapshot"])
    if FrozenMap(effect) != FrozenMap(mana_value_cast_instruction(actor=actor, snapshot=snapshot)):
        raise ValueError("The mana-value reveal changed its original player or cast facts")
    return snapshot


def mana_value_cast_payload_dependencies(effect):
    decode_mana_value_cast_instruction(effect, actor="$controller", allow_symbolic=True)
    return DEPENDENCIES


def mana_value_cast_program_dependencies(program):
    if (len(program.effects) != 1 or program.target_schema is not None or program.cost_schema is not None
            or program.event != "spell.cast" or program.active_zone != "battlefield"
            or HAND_CAST_CONDITION != FrozenMap(program.event_condition or {}) or program.handlers or program.trigger_limit is not None):
        raise ValueError("This reveal cast requires its complete battlefield from-hand trigger")
    return mana_value_cast_payload_dependencies(program.effects[0])
