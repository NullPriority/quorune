from __future__ import annotations

"""Closed opposing-player Blight descriptor and simultaneous placement identity."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import FrozenMap

OPPONENT_BLIGHT_OPERATION = "blight_opponents"
OPPONENT_BLIGHT_BATCH_KIND = "opponent_blight_batch"
OPPONENT_BLIGHT_MECHANIC = "fixed-opponents-blight"
OPPONENT_BLIGHT_CAPABILITY = "keyword_action.blight.fixed_opponents"
OPPONENT_BLIGHT_ENTRY_VARIANT = "source_or_controlled_subtype_entry"
OPPONENT_BLIGHT_REASON = "simultaneous opponent Blight"


def opponent_blight_effect(player="$controller"):
    return {
        "op": "choose_cards_apnap",
        "schema_version": 1,
        "players": "opponents",
        "actor": player,
        "count": 1,
        "zone": "battlefield",
        "then": OPPONENT_BLIGHT_OPERATION,
        "amount": 1,
    }


def uses_opponent_blight(effect):
    return isinstance(effect, Mapping) and effect.get("then") == OPPONENT_BLIGHT_OPERATION


def validate_opponent_blight_effect(effect, player="$controller"):
    if (
        not isinstance(effect, Mapping)
        or type(player) is not str
        or not player
        or type(effect.get("schema_version")) is not int
        or type(effect.get("amount")) is not int
        or type(effect.get("count")) is not int
        or FrozenMap(effect) != FrozenMap(opponent_blight_effect(player))
    ):
        raise ValueError("Opposing-player Blight requires one fixed untargeted instruction")


def opponent_blight_entry_condition(subtype):
    if type(subtype) is not str or canonical_creature_subtype(subtype) != subtype:
        raise ValueError("Opposing-player Blight requires a canonical entry subtype")
    return {
        "any": [
            {"field": "card", "op": "eq", "value": "$source.ref"},
            {"all": [
                {"field": "controller", "op": "eq", "value": "$source.controller"},
                {"field": "subtypes", "op": "contains_any", "value": [subtype]},
            ]},
        ],
    }


@dataclass(frozen=True, slots=True)
class OpponentBlightSelection:
    placing_player: str
    ref: str
    logical_object_id: str

    def __post_init__(self):
        if any(type(value) is not str or not value or value.startswith("$")
               for value in (self.placing_player, self.ref, self.logical_object_id)):
            raise ValueError("A Blight selection requires its player and permanent incarnation")

    def to_dict(self):
        return {
            "placing_player": self.placing_player,
            "ref": self.ref,
            "logical_object_id": self.logical_object_id,
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"placing_player", "ref", "logical_object_id"}:
            raise ValueError("Blight selection fields changed")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class OpponentBlightBatchIntent:
    actor: str
    source_ref: str
    selections: tuple[OpponentBlightSelection, ...]
    replacement_selections: tuple = ()

    def __post_init__(self):
        if any(type(value) is not str or not value or value.startswith("$")
               for value in (self.actor, self.source_ref)):
            raise ValueError("Opposing-player Blight needs its originating controller and source")
        if not self.selections or any(not isinstance(row, OpponentBlightSelection) for row in self.selections):
            raise ValueError("A Blight batch must place counters on at least one chosen permanent")
        for field in ("placing_player", "ref", "logical_object_id"):
            if len({getattr(row, field) for row in self.selections}) != len(self.selections):
                raise ValueError("Opposing-player Blight requires unique players and permanents")
        if any(row.placing_player == self.actor for row in self.selections):
            raise ValueError("The source's controller cannot be a placing opponent")
        from ..semantic_runtime.intents import _freeze_replacement_selections

        object.__setattr__(self, "selections", tuple(self.selections))
        object.__setattr__(self, "replacement_selections", _freeze_replacement_selections(
            tuple(self.replacement_selections), family="Opposing-player Blight",
        ))

    def identity(self):
        return {
            "schema_version": 1,
            "actor": self.actor,
            "source_ref": self.source_ref,
            "amount": 1,
            "reason": OPPONENT_BLIGHT_REASON,
            "selections": [row.to_dict() for row in self.selections],
        }

    @classmethod
    def from_identity(cls, value):
        fields = {"schema_version", "actor", "source_ref", "amount", "reason", "selections"}
        if (
            not isinstance(value, Mapping)
            or set(value) != fields
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["amount"]) is not int or value["amount"] != 1
            or value["reason"] != OPPONENT_BLIGHT_REASON
            or not isinstance(value["selections"], (list, tuple))
        ):
            raise ValueError("Opposing-player Blight identity fields changed")
        return cls(value["actor"], value["source_ref"], tuple(
            OpponentBlightSelection.from_dict(row) for row in value["selections"]
        ))




def fixed_opponent_blight_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or OPPONENT_BLIGHT_MECHANIC not in mechanic_ids:
        return ()
    try:
        validate_opponent_blight_effect(effects[0])
    except ValueError:
        return ()
    return (OPPONENT_BLIGHT_CAPABILITY,)


def opponent_blight_program_dependencies(program):
    if not any(uses_opponent_blight(effect) for effect in program.effects):
        return ()
    if program.event != "permanent.enter" or program.active_zone != "battlefield" or program.target_schema is not None or len(program.effects) != 1:
        raise ValueError("Opposing-player Blight requires its nontargeted battlefield entry trigger")
    validate_opponent_blight_effect(program.effects[0])
    condition = FrozenMap(program.event_condition or {})
    try:
        subtype, = condition["any"][1]["all"][1]["value"]
        expected = FrozenMap(opponent_blight_entry_condition(subtype))
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Opposing-player Blight requires its closed source-or-subtype binding") from exc
    if condition != expected:
        raise ValueError("Opposing-player Blight entry condition changed")
    return (OPPONENT_BLIGHT_CAPABILITY,)
