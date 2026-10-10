from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


class CombatDamageSnapshotError(ValueError):
    """The authoritative combat state cannot form a closed damage snapshot."""


def _identity(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CombatDamageSnapshotError(f"{label} must be a nonempty string")
    return value


def _exact_integer(value: object, *, label: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CombatDamageSnapshotError(f"{label} must be an exact integer")
    if minimum is not None and value < minimum:
        raise CombatDamageSnapshotError(f"{label} cannot be less than {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class CombatDamageParticipant:
    """Current effective characteristics for one physical combatant."""

    object_id: str
    reference: str
    controller: str
    power: int
    toughness: int
    marked_damage: int
    keywords: frozenset[str]
    assigns_damage: bool
    logical_object_id: str = ""
    assign_as_unblocked: bool = False
    must_assign_as_unblocked: bool = False
    assigns_using_toughness: bool = False

    def __post_init__(self) -> None:
        if type(self.assign_as_unblocked) is not bool:raise CombatDamageSnapshotError('As-unblocked assignment permission must be boolean')
        if type(self.must_assign_as_unblocked) is not bool:raise CombatDamageSnapshotError('As-unblocked assignment rule must be boolean')
        if type(self.assigns_using_toughness) is not bool:raise CombatDamageSnapshotError('Toughness assignment rule must be boolean')
        _identity(self.object_id, label="Participant object identity")
        _identity(self.reference, label="Participant reference")
        _identity(self.controller, label="Participant controller")
        logical = self.logical_object_id or self.object_id
        _identity(logical, label="Participant logical identity")
        object.__setattr__(self, "logical_object_id", logical)
        _exact_integer(self.power, label="Participant power")
        _exact_integer(self.toughness, label="Participant toughness")
        _exact_integer(
            self.marked_damage,
            label="Participant marked damage",
            minimum=0,
        )
        if not isinstance(self.assigns_damage, bool):
            raise CombatDamageSnapshotError(
                "Participant damage-step eligibility must be boolean"
            )
        if isinstance(self.keywords, (str, bytes)):
            raise CombatDamageSnapshotError(
                "Participant abilities must be a collection of keywords"
            )
        object.__setattr__(
            self,
            "keywords",
            frozenset(
                _identity(value, label="Participant ability").casefold()
                for value in self.keywords
            ),
        )

    @property
    def abilities(self) -> frozenset[str]:
        return self.keywords


@dataclass(frozen=True, slots=True)
class CombatDamageRecipient:
    """The declaration-time attacked recipient and its current legality."""

    reference: str
    logical_object_id: str
    controller: str
    kind: str
    legal: bool
    object_id: str | None = None

    def __post_init__(self) -> None:
        _identity(self.reference, label="Attack recipient reference")
        _identity(
            self.logical_object_id,
            label="Attack recipient logical identity",
        )
        _identity(self.controller, label="Attack recipient controller")
        if self.kind not in {"player", "planeswalker", "battle"}:
            raise CombatDamageSnapshotError(
                f"Unsupported attack recipient kind {self.kind!r}"
            )
        if not isinstance(self.legal, bool):
            raise CombatDamageSnapshotError(
                "Attack recipient legality must be boolean"
            )
        if self.kind == "player":
            if self.object_id is not None:
                raise CombatDamageSnapshotError(
                    "Player attack recipients cannot have object identities"
                )
        else:
            _identity(self.object_id, label="Permanent recipient object identity")


@dataclass(frozen=True, slots=True)
class CombatAttackRelationship:
    source_object_id: str
    recipient: CombatDamageRecipient

    def __post_init__(self) -> None:
        _identity(self.source_object_id, label="Attacker object identity")
        if not isinstance(self.recipient, CombatDamageRecipient):
            raise CombatDamageSnapshotError(
                "Attack relationships require typed recipients"
            )


@dataclass(frozen=True, slots=True)
class CombatBlockRelationship:
    attacker_object_id: str
    blocker_object_id: str

    def __post_init__(self) -> None:
        _identity(self.attacker_object_id, label="Blocked attacker identity")
        _identity(self.blocker_object_id, label="Blocker object identity")


@dataclass(frozen=True, slots=True)
class CombatDamageSnapshot:
    """Immutable, closed input to CR 510 assignment and announcement logic."""

    damage_step_id: str
    damage_step_index: int
    first_strike_step: bool
    active_player: str
    participants: tuple[CombatDamageParticipant, ...]
    attacks: tuple[CombatAttackRelationship, ...]
    blocks: tuple[CombatBlockRelationship, ...]
    was_blocked: frozenset[str]

    def __post_init__(self) -> None:
        _identity(self.damage_step_id, label="Combat damage-step identity")
        _exact_integer(
            self.damage_step_index,
            label="Combat damage-step index",
            minimum=0,
        )
        if not isinstance(self.first_strike_step, bool):
            raise CombatDamageSnapshotError(
                "First-strike-step state must be boolean"
            )
        _identity(self.active_player, label="Active player")
        participants = tuple(self.participants)
        attacks = tuple(self.attacks)
        blocks = tuple(self.blocks)
        was_blocked = frozenset(self.was_blocked)
        object.__setattr__(self, "participants", participants)
        object.__setattr__(self, "attacks", attacks)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "was_blocked", was_blocked)

        if not all(isinstance(value, CombatDamageParticipant) for value in participants):
            raise CombatDamageSnapshotError(
                "Combat participants must be typed immutable values"
            )
        if not all(isinstance(value, CombatAttackRelationship) for value in attacks):
            raise CombatDamageSnapshotError(
                "Combat attacks must be typed immutable values"
            )
        if not all(isinstance(value, CombatBlockRelationship) for value in blocks):
            raise CombatDamageSnapshotError(
                "Combat blocks must be typed immutable values"
            )

        participant_ids = [value.object_id for value in participants]
        participant_refs = [value.reference for value in participants]
        participant_logical_ids = [value.logical_object_id for value in participants]
        for values, label in (
            (participant_ids, "object identities"),
            (participant_refs, "references"),
            (participant_logical_ids, "logical identities"),
        ):
            if len(values) != len(set(values)):
                raise CombatDamageSnapshotError(
                    f"Combat participant {label} must be unique"
                )

        participant_set = set(participant_ids)
        attack_ids = [value.source_object_id for value in attacks]
        if len(attack_ids) != len(set(attack_ids)):
            raise CombatDamageSnapshotError(
                "Each combat attacker must appear exactly once"
            )
        if not set(attack_ids) <= participant_set:
            raise CombatDamageSnapshotError(
                "Every current attacker must be a combat participant"
            )
        by_id = {value.object_id: value for value in participants}
        if any(
            by_id[attacker_id].controller != self.active_player
            for attacker_id in attack_ids
        ):
            raise CombatDamageSnapshotError(
                "Every current attacker must be controlled by the active player"
            )
        block_pairs = [
            (value.attacker_object_id, value.blocker_object_id)
            for value in blocks
        ]
        if len(block_pairs) != len(set(block_pairs)):
            raise CombatDamageSnapshotError(
                "Combat blocker relationships must be unique"
            )
        if any(
            attacker not in set(attack_ids) or blocker not in participant_set
            for attacker, blocker in block_pairs
        ):
            raise CombatDamageSnapshotError(
                "Combat blocker relationships must be internally closed"
            )
        blocker_ids = [blocker for _attacker, blocker in block_pairs]
        if set(attack_ids).intersection(blocker_ids):
            raise CombatDamageSnapshotError(
                "A represented participant cannot be both attacker and blocker"
            )
        if participant_set != set(attack_ids).union(blocker_ids):
            raise CombatDamageSnapshotError(
                "Every combat participant must have one closed relationship"
            )
        if not was_blocked <= set(attack_ids):
            raise CombatDamageSnapshotError(
                "Historical was-blocked identities must be current attackers"
            )
        if not {attacker for attacker, _blocker in block_pairs} <= was_blocked:
            raise CombatDamageSnapshotError(
                "Every currently blocked attacker must be marked was-blocked"
            )


class CombatDamageQuery(Protocol):
    """Read-only adapter used to build a combat-damage snapshot."""

    def damage_step_identity(self) -> str: ...

    def damage_step_index(self) -> int: ...

    def first_strike_step(self) -> bool: ...

    def active_player(self) -> str: ...

    def participant_object_ids(self) -> Sequence[str]: ...

    def participant(self, object_id: str) -> CombatDamageParticipant: ...

    def attacker_object_ids(self) -> Sequence[str]: ...

    def attack_recipient(self, attacker_object_id: str) -> CombatDamageRecipient: ...

    def blocker_object_ids(self, attacker_object_id: str) -> Sequence[str]: ...

    def was_blocked(self, attacker_object_id: str) -> bool: ...


def build_combat_damage_snapshot(query: CombatDamageQuery) -> CombatDamageSnapshot:
    """Read one coherent state view and fail closed on malformed relations."""

    participant_ids = tuple(query.participant_object_ids())
    if len(participant_ids) != len(set(participant_ids)) or not all(participant_ids):
        raise CombatDamageSnapshotError(
            "Query participant identities must be unique and nonempty"
        )
    participants = tuple(query.participant(object_id) for object_id in participant_ids)
    if tuple(value.object_id for value in participants) != participant_ids:
        raise CombatDamageSnapshotError(
            "Query participants must preserve their requested physical identities"
        )
    canonical_participants = tuple(
        sorted(participants, key=lambda value: (value.reference, value.object_id))
    )

    attacker_ids = tuple(query.attacker_object_ids())
    if len(attacker_ids) != len(set(attacker_ids)) or not all(attacker_ids):
        raise CombatDamageSnapshotError(
            "Query attacker identities must be unique and nonempty"
        )
    by_id = {value.object_id: value for value in canonical_participants}
    for attacker_id in attacker_ids:
        if attacker_id not in by_id:
            raise CombatDamageSnapshotError(
                "Every query attacker must be a current combat participant"
            )

    attacks = tuple(
        sorted(
            (
                CombatAttackRelationship(
                    source_object_id=attacker_id,
                    recipient=query.attack_recipient(attacker_id),
                )
                for attacker_id in attacker_ids
            ),
            key=lambda value: (
                by_id[value.source_object_id].reference,
                value.source_object_id,
            ),
        )
    )
    blocks: list[CombatBlockRelationship] = []
    was_blocked: set[str] = set()
    for attack in attacks:
        attacker_id = attack.source_object_id
        blocker_ids = tuple(query.blocker_object_ids(attacker_id))
        if len(blocker_ids) != len(set(blocker_ids)) or not all(blocker_ids):
            raise CombatDamageSnapshotError(
                "Query blocker identities must be unique and nonempty"
            )
        if query.was_blocked(attacker_id):
            was_blocked.add(attacker_id)
        for blocker_id in blocker_ids:
            if blocker_id not in by_id:
                raise CombatDamageSnapshotError(
                    "Every query blocker must be a current combat participant"
                )
            blocks.append(
                CombatBlockRelationship(
                    attacker_object_id=attacker_id,
                    blocker_object_id=blocker_id,
                )
            )
    blocks.sort(
        key=lambda value: (
            by_id[value.attacker_object_id].reference,
            by_id[value.blocker_object_id].reference,
        )
    )
    return CombatDamageSnapshot(
        damage_step_id=query.damage_step_identity(),
        damage_step_index=query.damage_step_index(),
        first_strike_step=query.first_strike_step(),
        active_player=query.active_player(),
        participants=canonical_participants,
        attacks=attacks,
        blocks=tuple(blocks),
        was_blocked=frozenset(was_blocked),
    )


__all__ = [
    "build_combat_damage_snapshot",
    "CombatAttackRelationship",
    "CombatBlockRelationship",
    "CombatDamageParticipant",
    "CombatDamageQuery",
    "CombatDamageRecipient",
    "CombatDamageSnapshot",
    "CombatDamageSnapshotError",
]
