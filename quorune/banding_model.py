from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping


BANDING_CAPABILITY = 'combat.banding'


@dataclass(frozen=True, slots=True)
class AttackBand:
    """One declared CR702.22c band of current physical incarnations."""
    members: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if not isinstance(self.members, (list, tuple)) or not self.members:
            raise ValueError('Attack band requires a nonempty member array')
        if any(not isinstance(value, (list, tuple)) for value in self.members):
            raise ValueError('Attack band members require identity pairs')
        values = tuple(tuple(value) for value in self.members)
        if any(len(value) != 2 or any(type(part) is not str or not part for part in value) for value in values):
            raise ValueError('Attack band members require physical and logical identity')
        if len({value[0] for value in values}) != len(values):
            raise ValueError('Attack band members must be distinct')
        object.__setattr__(self, 'members', tuple(sorted(values)))

    def to_dict(self):
        return {'members': [{'object_id': oid, 'logical_object_id': logical} for oid, logical in self.members]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'members'} or not isinstance(value['members'], (list, tuple)):
            raise ValueError('Attack band fields are closed')
        if any(not isinstance(row, Mapping) or set(row) != {'object_id', 'logical_object_id'} for row in value['members']):
            raise ValueError('Attack band member fields are closed')
        return cls(tuple((row['object_id'], row['logical_object_id']) for row in value['members']))


def normalize_attack_bands(values):
    if not isinstance(values, (list, tuple)):
        raise ValueError('Attack bands must be an array')
    bands = tuple(value if isinstance(value, AttackBand) else AttackBand.from_dict(value) for value in values)
    members = [oid for band in bands for oid, _logical in band.members]
    if len(members) != len(set(members)):
        raise ValueError('An attacker cannot belong to two bands')
    return tuple(sorted(bands, key=lambda band: band.members))


def band_member_ids(combat, cards, object_id):
    for band in combat.attack_bands:
        current = tuple(oid for oid, logical in band.members if oid in combat.attackers
            and oid in cards and cards[oid].logical_object_id == logical)
        if object_id in current:
            return current
    return (object_id,)


def combat_assignment_actor(snapshot, participant, participants, attacks, blockers, blocked_attackers):
    """CR702.22j/k changes the chooser, never the damage source controller."""
    if participant.object_id in attacks:
        banding_blockers = [participants[oid] for oid in blockers[participant.object_id]
            if 'banding' in participants[oid].keywords]
        if banding_blockers:
            return banding_blockers[0].controller
    elif any('banding' in participants[oid].keywords for oid in blocked_attackers[participant.object_id]):
        return snapshot.active_player
    return participant.controller


def projected_attack_bands(combat, cards):
    return [[cards[oid].ref for oid, logical in band.members
        if oid in combat.attackers and oid in cards and cards[oid].logical_object_id == logical]
        for band in combat.attack_bands]
