"""Immutable actual damage received by one incarnation in one transaction."""
from collections.abc import Mapping
from dataclasses import dataclass

from .creature_subtypes import canonical_creature_subtype

FIXED_DAMAGE_REMAINDER_HANDLER = "prevention.damage.fixed_remainder.v1"
FIXED_DAMAGE_REMAINDER_CAPABILITY = "damage.prevention.fixed_remainder"


@dataclass(frozen=True, slots=True)
class FixedDamageRemainderScope:
    kind: str
    subtype: str | None = None

    def __post_init__(self):
        if self.kind == "controller_or_planeswalker" and self.subtype is None:
            return
        if (self.kind not in {"other_controlled_subtype", "controller_or_controlled_subtype"}
                or type(self.subtype) is not str
                or canonical_creature_subtype(self.subtype) != self.subtype):
            raise ValueError("Fixed damage remainder requires a closed live recipient scope")

    def descriptor(self):
        return {
            "handler_id": FIXED_DAMAGE_REMAINDER_HANDLER, "schema_version": 1,
            "event": "damage", "condition": {"kind": self.kind, "subtype": self.subtype},
            "modification": {"damage_remaining": 1},
        }

    @classmethod
    def from_descriptor(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"handler_id", "schema_version", "event", "condition", "modification"}
                or value["handler_id"] != FIXED_DAMAGE_REMAINDER_HANDLER or type(value["schema_version"]) is not int
                or value["schema_version"] != 1 or value["event"] != "damage"):
            raise ValueError("Fixed damage remainder requires its exact damage component")
        condition, modification = value["condition"], value["modification"]
        if (not isinstance(condition, Mapping) or set(condition) != {"kind", "subtype"}
                or type(condition["kind"]) is not str or not isinstance(modification, Mapping)
                or set(modification) != {"damage_remaining"}
                or type(modification["damage_remaining"]) is not int or modification["damage_remaining"] != 1):
            raise ValueError("Fixed damage remainder requires one explicit retained damage")
        return cls(condition["kind"], condition["subtype"])


def is_command_fixed_damage_remainder(descriptor):
    try:
        return FixedDamageRemainderScope.from_descriptor(descriptor).kind == "controller_or_planeswalker"
    except (TypeError, ValueError):
        return False


RECEIVED_DAMAGE_EVENT = 'damage.received'
RECEIVED_COMBAT_DAMAGE_EVENT = 'combat.damage.received'
RECEIVED_DAMAGE_EVENTS = frozenset({RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT})
RECEIVED_DAMAGE_CAPABILITY = 'trigger.event.normalized_received_damage'
RECEIVED_DAMAGE_MECHANIC = 'trigger-event-normalized-received-damage'


@dataclass(frozen=True, slots=True)
class ReceivedDamageOccurrence:
    event_id: str
    target: str
    target_kind: str
    target_object_id: str | None
    target_logical_object_id: str | None
    target_controller: str | None
    target_types: tuple[str, ...]
    target_subtypes: tuple[str, ...]
    amount: int
    combat_amount: int

    def __post_init__(self) -> None:
        if any(type(v) is not str or not v for v in (self.event_id, self.target)):
            raise ValueError('Received damage requires batch and recipient identities')
        if self.target_kind not in {'player', 'permanent'}:
            raise ValueError('Received damage recipient kind is closed')
        if any(type(v) is not int for v in (self.amount, self.combat_amount)) or not 0 <= self.combat_amount <= self.amount or self.amount <= 0:
            raise ValueError('Received amounts must be exact positive committed damage')
        identities = (self.target_object_id, self.target_logical_object_id)
        if self.target_kind == 'permanent':
            if any(type(v) is not str or not v for v in (*identities, self.target_controller)):
                raise ValueError('A permanent receiving damage needs its current incarnation')
        elif any(v is not None for v in identities):
            raise ValueError('Player receiving events have no permanent incarnation')
        if self.target_controller is not None and (type(self.target_controller) is not str or not self.target_controller):
            raise ValueError('Received-damage controller must be represented or absent')
        for values in (self.target_types, self.target_subtypes):
            if not isinstance(values, tuple) or any(type(v) is not str or not v for v in values) or values != tuple(sorted(set(values))):
                raise ValueError('Received-damage type facts must be canonical tuples')

    def semantic_context(self, *, combat_only: bool = False) -> dict:
        if type(combat_only) is not bool or combat_only and not self.combat_amount:
            raise ValueError('A combat receiving event requires actual combat damage')
        return {
            'event_id': self.event_id, 'card': self.target,
            'object_id': self.target_object_id, 'logical_object_id': self.target_logical_object_id,
            'controller': self.target_controller, 'types': list(self.target_types),
            'subtypes': list(self.target_subtypes), 'target': self.target,
            'target_kind': self.target_kind, 'target_object_id': self.target_object_id,
            'target_logical_object_id': self.target_logical_object_id,
            'target_controller': self.target_controller, 'target_types': list(self.target_types),
            'target_subtypes': list(self.target_subtypes),
            'player': self.target if self.target_kind == 'player' else self.target_controller,
            'amount': self.combat_amount if combat_only else self.amount,
            'combat_amount': self.combat_amount,
            'combat': combat_only or self.combat_amount == self.amount,
        }


RECEIVED_DAMAGE_WATCH_FIELD = "received_damage_watch"
RECEIVED_DAMAGE_WATCH_LABEL = "The watched creature deals the damage it received"


@dataclass(frozen=True, slots=True)
class ReceivedDamageWatch:
    """One original battlefield incarnation and the resolving spell that named it."""

    object_id: str
    logical_object_id: str
    source_stack_ref: str

    def __post_init__(self):
        if (
            any(type(value) is not str or not value for value in (
                self.object_id, self.logical_object_id, self.source_stack_ref,
            ))
            or self.logical_object_id.rpartition("@")[0] != self.object_id
        ):
            raise ValueError("A received damage watch requires the original object and spell identities")

    def to_dict(self):
        return {
            "schema_version": 1, "object_id": self.object_id,
            "logical_object_id": self.logical_object_id, "source_stack_ref": self.source_stack_ref,
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {
                "schema_version", "object_id", "logical_object_id", "source_stack_ref",
        } or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("A received damage watch requires its exact versioned fields")
        return cls(value["object_id"], value["logical_object_id"], value["source_stack_ref"])

    @property
    def condition(self):
        return {"target_kind": "permanent", "target_object_id": self.object_id,
                "target_logical_object_id": self.logical_object_id}

    @property
    def stack_template(self):
        return {"label": RECEIVED_DAMAGE_WATCH_LABEL, RECEIVED_DAMAGE_WATCH_FIELD: self.to_dict()}

    @classmethod
    def from_trigger(cls, trigger):
        watch = cls.from_dict(trigger.stack_template.get(RECEIVED_DAMAGE_WATCH_FIELD))
        if (
            trigger.event_kind != RECEIVED_DAMAGE_EVENT or trigger.once is not False
            or type(trigger.created_turn_sequence) is not int or trigger.created_turn_sequence < 0
            or type(trigger.expires_turn_sequence) is not int
            or trigger.expires_turn_sequence != trigger.created_turn_sequence
            or trigger.condition != watch.condition or trigger.stack_template != watch.stack_template
            or trigger.label != RECEIVED_DAMAGE_WATCH_LABEL
            or tuple(trigger.referred_object_ids) != (watch.object_id,)
        ):
            raise ValueError("A received damage watch requires the original this-turn repeating schedule")
        return watch
