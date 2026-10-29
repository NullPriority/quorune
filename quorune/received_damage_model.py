"""Immutable actual damage received by one incarnation in one transaction."""
from dataclasses import dataclass

RECEIVED_DAMAGE_EVENT = 'damage.received'
RECEIVED_COMBAT_DAMAGE_EVENT = 'combat.damage.received'
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
