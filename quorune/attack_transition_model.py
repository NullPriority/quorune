from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any, Mapping, Protocol, Sequence

from .ability_fragments import (
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
)
from .util import stable_json


ATTACK_TRIGGER_KINDS = frozenset(
    {
        CombatKeywordTriggerKind.EXALTED,
        CombatKeywordTriggerKind.BATTLE_CRY,
        CombatKeywordTriggerKind.MELEE,
        CombatKeywordTriggerKind.MENTOR,
        CombatKeywordTriggerKind.DETHRONE,
        CombatKeywordTriggerKind.TRAINING,
    }
)

UNTARGETED_ATTACK_TRIGGER_KINDS = frozenset(
    {
        CombatKeywordTriggerKind.EXALTED,
        CombatKeywordTriggerKind.BATTLE_CRY,
        CombatKeywordTriggerKind.MELEE,
    }
)


class AttackTransitionError(ValueError):
    """A declaration-time attack transition is malformed or stale."""


def _identity(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise AttackTransitionError(f"{field} must be a nonempty string")
    return value


def _integer(value: Any, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AttackTransitionError(
            f"{field} must be an exact integer no less than {minimum}"
        )
    return value


def _exact_mapping(
    value: Any,
    expected: set[str],
    *,
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise AttackTransitionError(f"{field} has a closed field set")
    return value


@dataclass(frozen=True, slots=True)
class AttackObjectIdentity:
    object_id: str
    logical_object_id: str
    reference: str

    def __post_init__(self) -> None:
        _identity(self.object_id, field="Attack object physical identity")
        _identity(
            self.logical_object_id,
            field="Attack object logical identity",
        )
        _identity(self.reference, field="Attack object reference")

    def to_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "reference": self.reference,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AttackObjectIdentity":
        data = _exact_mapping(
            value,
            {"object_id", "logical_object_id", "reference"},
            field="Attack object identity",
        )
        return cls(**dict(data))


@dataclass(frozen=True, slots=True)
class AttackTransitionParticipant:
    object_id: str
    logical_object_id: str
    reference: str
    controller: str
    is_creature: bool
    trigger_specs: tuple[CombatKeywordTriggerSpec, ...] = ()
    power: int | None = None
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identity(self.object_id, field="Attack participant physical identity")
        _identity(
            self.logical_object_id,
            field="Attack participant logical identity",
        )
        _identity(self.reference, field="Attack participant reference")
        _identity(self.controller, field="Attack participant controller")
        if type(self.is_creature) is not bool:
            raise AttackTransitionError(
                "Attack participant is_creature must be a boolean"
            )
        if self.power is not None and type(self.power) is not int:
            raise AttackTransitionError(
                "Attack participant power must be an exact integer or null"
            )
        raw_keywords = self.keywords
        if not isinstance(raw_keywords, tuple) or any(
            type(value) is not str or not value.strip()
            for value in raw_keywords
        ):
            raise AttackTransitionError(
                "Attack participant keywords must be a tuple of nonempty strings"
            )
        keywords = tuple(
            sorted(
                {
                    value.strip().casefold()
                    for value in raw_keywords
                }
            )
        )
        if len(keywords) != len(raw_keywords):
            raise AttackTransitionError(
                "Attack participant keywords must be distinct nonempty strings"
            )
        object.__setattr__(self, "keywords", keywords)
        specs = tuple(self.trigger_specs)
        if any(
            not isinstance(spec, CombatKeywordTriggerSpec)
            or spec.kind not in ATTACK_TRIGGER_KINDS
            for spec in specs
        ):
            raise AttackTransitionError(
                "Attack participants require typed attack-trigger fragments"
            )
        object.__setattr__(
            self,
            "trigger_specs",
            tuple(
                sorted(specs, key=lambda spec: stable_json(spec.to_dict()))
            ),
        )

    @property
    def identity(self) -> AttackObjectIdentity:
        return AttackObjectIdentity(
            object_id=self.object_id,
            logical_object_id=self.logical_object_id,
            reference=self.reference,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            **self.identity.to_dict(),
            "controller": self.controller,
            "is_creature": self.is_creature,
            "trigger_specs": [spec.to_dict() for spec in self.trigger_specs],
        }
        if self.power is not None:
            result["power"] = self.power
        if self.keywords:
            result["keywords"] = list(self.keywords)
        return result

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "AttackTransitionParticipant":
        fields = set(value) if isinstance(value, Mapping) else set()
        expected = {
            "object_id",
            "logical_object_id",
            "reference",
            "controller",
            "is_creature",
            "trigger_specs",
        }
        allowed = {
            frozenset(expected),
            frozenset({*expected, "power"}),
            frozenset({*expected, "keywords"}),
            frozenset({*expected, "power", "keywords"}),
        }
        if fields not in allowed:
            raise AttackTransitionError(
                "Attack transition participant has a closed field set"
            )
        data = value
        raw_specs = data["trigger_specs"]
        if not isinstance(raw_specs, (list, tuple)):
            raise AttackTransitionError(
                "Attack participant trigger_specs must be an array"
            )
        raw_keywords = data.get("keywords", ())
        if not isinstance(raw_keywords, (list, tuple)) or isinstance(
            raw_keywords, (str, bytes)
        ):
            raise AttackTransitionError(
                "Attack participant keywords must be an array"
            )
        return cls(
            object_id=data["object_id"],
            logical_object_id=data["logical_object_id"],
            reference=data["reference"],
            controller=data["controller"],
            is_creature=data["is_creature"],
            trigger_specs=tuple(
                CombatKeywordTriggerSpec.from_dict(spec)
                for spec in raw_specs
            ),
            power=data.get("power"),
            keywords=tuple(raw_keywords),
        )


class AttackRecipientKind(str, Enum):
    PLAYER = "player"
    PLANESWALKER = "planeswalker"
    BATTLE = "battle"


@dataclass(frozen=True, slots=True)
class AttackRecipient:
    kind: AttackRecipientKind
    reference: str
    defending_player: str
    logical_object_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AttackRecipientKind):
            raise AttackTransitionError("Unsupported attack-recipient kind")
        _identity(self.reference, field="Attack recipient reference")
        _identity(
            self.defending_player,
            field="Attack recipient defending player",
        )
        if self.kind is AttackRecipientKind.PLAYER:
            if self.logical_object_id is not None:
                raise AttackTransitionError(
                    "Player attack recipients have no logical object identity"
                )
            if self.reference != self.defending_player:
                raise AttackTransitionError(
                    "A player recipient must identify the defending player"
                )
        elif type(self.logical_object_id) is not str or not self.logical_object_id:
            raise AttackTransitionError(
                "Permanent attack recipients require logical identity"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reference": self.reference,
            "defending_player": self.defending_player,
            "logical_object_id": self.logical_object_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AttackRecipient":
        data = _exact_mapping(
            value,
            {
                "kind",
                "reference",
                "defending_player",
                "logical_object_id",
            },
            field="Attack recipient",
        )
        try:
            kind = AttackRecipientKind(data["kind"])
        except (TypeError, ValueError) as exc:
            raise AttackTransitionError(
                "Unsupported attack-recipient kind"
            ) from exc
        return cls(
            kind=kind,
            reference=data["reference"],
            defending_player=data["defending_player"],
            logical_object_id=data["logical_object_id"],
        )


@dataclass(frozen=True, slots=True)
class AttackTransitionAssignment:
    attacker_object_id: str
    recipient: AttackRecipient

    def __post_init__(self) -> None:
        _identity(
            self.attacker_object_id,
            field="Attack transition attacker identity",
        )
        if not isinstance(self.recipient, AttackRecipient):
            raise AttackTransitionError(
                "Attack transition assignments require a typed recipient"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "attacker_object_id": self.attacker_object_id,
            "recipient": self.recipient.to_dict(),
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "AttackTransitionAssignment":
        data = _exact_mapping(
            value,
            {"attacker_object_id", "recipient"},
            field="Attack transition assignment",
        )
        return cls(
            attacker_object_id=data["attacker_object_id"],
            recipient=AttackRecipient.from_dict(data["recipient"]),
        )


def _transition_payload(
    *,
    turn_sequence: int,
    priority_epoch: int,
    active_player: str,
    participants: Sequence[AttackTransitionParticipant],
    assignments: Sequence[AttackTransitionAssignment],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "turn_sequence": turn_sequence,
        "priority_epoch": priority_epoch,
        "active_player": active_player,
        "participants": [value.to_dict() for value in participants],
        "assignments": [value.to_dict() for value in assignments],
    }


def _transition_id(payload: Mapping[str, Any]) -> str:
    return "attack-transition:" + hashlib.sha256(
        stable_json(payload).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class AttackTransitionEvent:
    transition_id: str
    turn_sequence: int
    priority_epoch: int
    active_player: str
    participants: tuple[AttackTransitionParticipant, ...]
    assignments: tuple[AttackTransitionAssignment, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        _identity(self.transition_id, field="Attack transition identity")
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise AttackTransitionError(
                "Unsupported attack-transition schema version"
            )
        _integer(self.turn_sequence, field="Attack transition turn sequence")
        _integer(self.priority_epoch, field="Attack transition priority epoch")
        _identity(self.active_player, field="Attack transition active player")
        participants = tuple(self.participants)
        assignments = tuple(self.assignments)
        if not participants or not assignments:
            raise AttackTransitionError(
                "An attack transition requires participants and assignments"
            )
        if any(
            not isinstance(value, AttackTransitionParticipant)
            for value in participants
        ) or any(
            not isinstance(value, AttackTransitionAssignment)
            for value in assignments
        ):
            raise AttackTransitionError(
                "Attack transitions require typed immutable values"
            )
        object.__setattr__(self, "participants", participants)
        object.__setattr__(self, "assignments", assignments)

        for values, label in (
            ([value.object_id for value in participants], "physical identities"),
            (
                [value.logical_object_id for value in participants],
                "logical identities",
            ),
            ([value.reference for value in participants], "references"),
        ):
            if len(values) != len(set(values)):
                raise AttackTransitionError(
                    f"Attack participant {label} must be unique"
                )
        by_id = {value.object_id: value for value in participants}
        attacker_ids = [value.attacker_object_id for value in assignments]
        if len(attacker_ids) != len(set(attacker_ids)):
            raise AttackTransitionError(
                "Each declared attacker requires exactly one recipient"
            )
        if any(object_id not in by_id for object_id in attacker_ids):
            raise AttackTransitionError(
                "Attack assignments must be closed over participants"
            )
        if any(
            by_id[object_id].controller != self.active_player
            or not by_id[object_id].is_creature
            for object_id in attacker_ids
        ):
            raise AttackTransitionError(
                "Every attacker must be a creature controlled by the active player"
            )
        if any(
            value.controller != self.active_player for value in participants
        ):
            raise AttackTransitionError(
                "Every represented attack-trigger source must be controlled by "
                "the active player"
            )
        attacker_set = set(attacker_ids)
        if any(
            value.object_id not in attacker_set and not value.trigger_specs
            for value in participants
        ):
            raise AttackTransitionError(
                "Nonattacking participants require an attack-trigger fragment"
            )
        if any(
            value.recipient.defending_player == self.active_player
            for value in assignments
        ):
            raise AttackTransitionError(
                "The active player cannot be an attack recipient"
            )
        canonical_participants = tuple(
            sorted(
                participants,
                key=lambda value: (value.reference, value.object_id),
            )
        )
        canonical_assignments = tuple(
            sorted(
                assignments,
                key=lambda value: (
                    by_id[value.attacker_object_id].reference,
                    value.attacker_object_id,
                ),
            )
        )
        if (
            participants != canonical_participants
            or assignments != canonical_assignments
        ):
            raise AttackTransitionError(
                "Attack transition values must use canonical public order"
            )
        payload = _transition_payload(
            turn_sequence=self.turn_sequence,
            priority_epoch=self.priority_epoch,
            active_player=self.active_player,
            participants=participants,
            assignments=assignments,
        )
        if self.transition_id != _transition_id(payload):
            raise AttackTransitionError(
                "Attack transition identity does not match its contents"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            **_transition_payload(
                turn_sequence=self.turn_sequence,
                priority_epoch=self.priority_epoch,
                active_player=self.active_player,
                participants=self.participants,
                assignments=self.assignments,
            ),
            "transition_id": self.transition_id,
        }

    @classmethod
    def create(
        cls,
        *,
        turn_sequence: int,
        priority_epoch: int,
        active_player: str,
        participants: Sequence[AttackTransitionParticipant],
        assignments: Sequence[AttackTransitionAssignment],
    ) -> "AttackTransitionEvent":
        participant_values = tuple(participants)
        assignment_values = tuple(assignments)
        payload = _transition_payload(
            turn_sequence=turn_sequence,
            priority_epoch=priority_epoch,
            active_player=active_player,
            participants=participant_values,
            assignments=assignment_values,
        )
        return cls(
            transition_id=_transition_id(payload),
            turn_sequence=turn_sequence,
            priority_epoch=priority_epoch,
            active_player=active_player,
            participants=participant_values,
            assignments=assignment_values,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AttackTransitionEvent":
        data = _exact_mapping(
            value,
            {
                "schema_version",
                "transition_id",
                "turn_sequence",
                "priority_epoch",
                "active_player",
                "participants",
                "assignments",
            },
            field="Attack transition event",
        )
        raw_participants = data["participants"]
        raw_assignments = data["assignments"]
        if not isinstance(raw_participants, (list, tuple)) or not isinstance(
            raw_assignments, (list, tuple)
        ):
            raise AttackTransitionError(
                "Attack transition participants and assignments must be arrays"
            )
        return cls(
            schema_version=data["schema_version"],
            transition_id=data["transition_id"],
            turn_sequence=data["turn_sequence"],
            priority_epoch=data["priority_epoch"],
            active_player=data["active_player"],
            participants=tuple(
                AttackTransitionParticipant.from_dict(item)
                for item in raw_participants
            ),
            assignments=tuple(
                AttackTransitionAssignment.from_dict(item)
                for item in raw_assignments
            ),
        )


class AttackTransitionQuery(Protocol):
    def turn_sequence(self) -> int: ...

    def priority_epoch(self) -> int: ...

    def active_player(self) -> str: ...

    def attacker_object_ids(self) -> Sequence[str]: ...

    def trigger_source_object_ids(self) -> Sequence[str]: ...

    def participant(self, object_id: str) -> AttackTransitionParticipant: ...

    def recipient(self, attacker_object_id: str) -> AttackRecipient: ...


def build_attack_transition(
    query: AttackTransitionQuery,
) -> AttackTransitionEvent | None:
    """Capture one canonical public transition after attackers are declared."""

    attacker_ids = tuple(query.attacker_object_ids())
    source_ids = tuple(query.trigger_source_object_ids())
    for values, label in (
        (attacker_ids, "attackers"),
        (source_ids, "trigger sources"),
    ):
        if len(values) != len(set(values)) or any(not value for value in values):
            raise AttackTransitionError(
                f"Attack transition {label} must be unique and nonempty"
            )
    if not attacker_ids:
        return None
    participant_ids = set(attacker_ids) | set(source_ids)
    participants_by_id: dict[str, AttackTransitionParticipant] = {}
    for object_id in participant_ids:
        participant = query.participant(object_id)
        if participant.object_id != object_id:
            raise AttackTransitionError(
                "Attack query participant identity changed while capturing"
            )
        participants_by_id[object_id] = participant
    participants = tuple(
        sorted(
            participants_by_id.values(),
            key=lambda value: (value.reference, value.object_id),
        )
    )
    assignments = tuple(
        sorted(
            (
                AttackTransitionAssignment(
                    attacker_object_id=object_id,
                    recipient=query.recipient(object_id),
                )
                for object_id in attacker_ids
            ),
            key=lambda value: (
                participants_by_id[value.attacker_object_id].reference,
                value.attacker_object_id,
            ),
        )
    )
    return AttackTransitionEvent.create(
        turn_sequence=query.turn_sequence(),
        priority_epoch=query.priority_epoch(),
        active_player=query.active_player(),
        participants=participants,
        assignments=assignments,
    )


def _occurrence_payload(
    *,
    transition_id: str,
    kind: CombatKeywordTriggerKind,
    controller: str,
    source: AttackObjectIdentity,
    affected: Sequence[AttackObjectIdentity],
    amount: int,
    instance_index: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "transition_id": transition_id,
        "kind": kind.value,
        "controller": controller,
        "source": source.to_dict(),
        "affected": [value.to_dict() for value in affected],
        "amount": amount,
        "instance_index": instance_index,
    }


def _occurrence_id(payload: Mapping[str, Any]) -> str:
    return "attack-trigger:" + hashlib.sha256(
        stable_json(payload).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class AttackKeywordTriggerOccurrence:
    occurrence_id: str
    transition_id: str
    kind: CombatKeywordTriggerKind
    controller: str
    source: AttackObjectIdentity
    affected: tuple[AttackObjectIdentity, ...]
    amount: int
    instance_index: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        _identity(self.occurrence_id, field="Attack trigger occurrence identity")
        _identity(self.transition_id, field="Attack trigger transition identity")
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise AttackTransitionError(
                "Unsupported attack-trigger occurrence schema version"
            )
        if self.kind not in UNTARGETED_ATTACK_TRIGGER_KINDS:
            raise AttackTransitionError(
                "Unsupported untargeted attack-trigger kind"
            )
        _identity(self.controller, field="Attack trigger controller")
        if not isinstance(self.source, AttackObjectIdentity):
            raise AttackTransitionError(
                "Attack triggers require a typed source identity"
            )
        affected = tuple(self.affected)
        if any(
            not isinstance(value, AttackObjectIdentity) for value in affected
        ):
            raise AttackTransitionError(
                "Attack triggers require typed affected identities"
            )
        if len(affected) != len({value.object_id for value in affected}):
            raise AttackTransitionError(
                "Attack-trigger affected identities must be unique"
            )
        canonical = tuple(
            sorted(affected, key=lambda value: (value.reference, value.object_id))
        )
        if affected != canonical:
            raise AttackTransitionError(
                "Attack-trigger affected identities must use canonical order"
            )
        object.__setattr__(self, "affected", affected)
        _integer(self.amount, field="Attack trigger amount")
        _integer(self.instance_index, field="Attack trigger instance index")
        if self.kind is not CombatKeywordTriggerKind.MELEE and self.amount != 1:
            raise AttackTransitionError(
                "Exalted and Battle Cry occurrences have amount 1"
            )
        if (
            self.kind is CombatKeywordTriggerKind.EXALTED
            and len(affected) != 1
        ):
            raise AttackTransitionError(
                "Exalted modifies the one creature that attacked alone"
            )
        if (
            self.kind is CombatKeywordTriggerKind.MELEE
            and affected != (self.source,)
        ):
            raise AttackTransitionError(
                "Melee modifies its declared attacking source"
            )
        if (
            self.kind is CombatKeywordTriggerKind.BATTLE_CRY
            and self.source in affected
        ):
            raise AttackTransitionError(
                "Battle Cry modifies only other attacking creatures"
            )
        payload = _occurrence_payload(
            transition_id=self.transition_id,
            kind=self.kind,
            controller=self.controller,
            source=self.source,
            affected=affected,
            amount=self.amount,
            instance_index=self.instance_index,
        )
        if self.occurrence_id != _occurrence_id(payload):
            raise AttackTransitionError(
                "Attack trigger occurrence identity does not match its contents"
            )

    @property
    def power_delta(self) -> int:
        return self.amount

    @property
    def toughness_delta(self) -> int:
        return 0 if self.kind is CombatKeywordTriggerKind.BATTLE_CRY else self.amount

    @property
    def label(self) -> str:
        names = {
            CombatKeywordTriggerKind.EXALTED: "Exalted",
            CombatKeywordTriggerKind.BATTLE_CRY: "Battle Cry",
            CombatKeywordTriggerKind.MELEE: "Melee",
        }
        return f"{self.source.reference} — {names[self.kind]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            **_occurrence_payload(
                transition_id=self.transition_id,
                kind=self.kind,
                controller=self.controller,
                source=self.source,
                affected=self.affected,
                amount=self.amount,
                instance_index=self.instance_index,
            ),
            "occurrence_id": self.occurrence_id,
        }

    @classmethod
    def create(
        cls,
        *,
        transition_id: str,
        kind: CombatKeywordTriggerKind,
        controller: str,
        source: AttackObjectIdentity,
        affected: Sequence[AttackObjectIdentity],
        amount: int,
        instance_index: int,
    ) -> "AttackKeywordTriggerOccurrence":
        affected_values = tuple(affected)
        payload = _occurrence_payload(
            transition_id=transition_id,
            kind=kind,
            controller=controller,
            source=source,
            affected=affected_values,
            amount=amount,
            instance_index=instance_index,
        )
        return cls(
            occurrence_id=_occurrence_id(payload),
            transition_id=transition_id,
            kind=kind,
            controller=controller,
            source=source,
            affected=affected_values,
            amount=amount,
            instance_index=instance_index,
        )

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "AttackKeywordTriggerOccurrence":
        data = _exact_mapping(
            value,
            {
                "schema_version",
                "occurrence_id",
                "transition_id",
                "kind",
                "controller",
                "source",
                "affected",
                "amount",
                "instance_index",
            },
            field="Attack trigger occurrence",
        )
        try:
            kind = CombatKeywordTriggerKind(data["kind"])
        except (TypeError, ValueError) as exc:
            raise AttackTransitionError("Unsupported attack-trigger kind") from exc
        raw_affected = data["affected"]
        if not isinstance(raw_affected, (list, tuple)):
            raise AttackTransitionError(
                "Attack trigger affected identities must be an array"
            )
        return cls(
            schema_version=data["schema_version"],
            occurrence_id=data["occurrence_id"],
            transition_id=data["transition_id"],
            kind=kind,
            controller=data["controller"],
            source=AttackObjectIdentity.from_dict(data["source"]),
            affected=tuple(
                AttackObjectIdentity.from_dict(item) for item in raw_affected
            ),
            amount=data["amount"],
            instance_index=data["instance_index"],
        )


def derive_attack_keyword_trigger_occurrences(
    event: AttackTransitionEvent,
) -> tuple[AttackKeywordTriggerOccurrence, ...]:
    """Derive Exalted, Battle Cry, and Melee from one sealed declaration."""

    if not isinstance(event, AttackTransitionEvent):
        raise AttackTransitionError(
            "Attack keyword triggers require a typed transition event"
        )
    participants = {value.object_id: value for value in event.participants}
    attackers = tuple(
        participants[value.attacker_object_id] for value in event.assignments
    )
    attacker_ids = {value.object_id for value in attackers}
    direct_opponents = {
        value.recipient.reference
        for value in event.assignments
        if value.recipient.kind is AttackRecipientKind.PLAYER
    }
    drafts: list[
        tuple[
            CombatKeywordTriggerKind,
            AttackTransitionParticipant,
            tuple[AttackObjectIdentity, ...],
            int,
            int,
        ]
    ] = []
    if len(attackers) == 1:
        lone_attacker = attackers[0]
        for source in event.participants:
            for index, spec in enumerate(
                value
                for value in source.trigger_specs
                if value.kind is CombatKeywordTriggerKind.EXALTED
            ):
                drafts.append(
                    (
                        spec.kind,
                        source,
                        (lone_attacker.identity,),
                        1,
                        index,
                    )
                )
    for source in attackers:
        for kind in (
            CombatKeywordTriggerKind.BATTLE_CRY,
            CombatKeywordTriggerKind.MELEE,
        ):
            for index, spec in enumerate(
                value for value in source.trigger_specs if value.kind is kind
            ):
                affected = (
                    tuple(
                        sorted(
                            (
                                value.identity
                                for value in attackers
                                if value.object_id != source.object_id
                            ),
                            key=lambda value: (
                                value.reference,
                                value.object_id,
                            ),
                        )
                    )
                    if kind is CombatKeywordTriggerKind.BATTLE_CRY
                    else (source.identity,)
                )
                amount = len(direct_opponents) if kind is CombatKeywordTriggerKind.MELEE else 1
                drafts.append((kind, source, affected, amount, index))
    if {value.object_id for value in attackers} != attacker_ids:
        raise AttackTransitionError("Attack participant closure changed")
    drafts.sort(
        key=lambda value: (
            value[1].reference,
            value[0].value,
            tuple(item.reference for item in value[2]),
            value[4],
        )
    )
    return tuple(
        AttackKeywordTriggerOccurrence.create(
            transition_id=event.transition_id,
            kind=kind,
            controller=source.controller,
            source=source.identity,
            affected=affected,
            amount=amount,
            instance_index=index,
        )
        for kind, source, affected, amount, index in drafts
    )


__all__ = [
    "ATTACK_TRIGGER_KINDS",
    "UNTARGETED_ATTACK_TRIGGER_KINDS",
    "AttackKeywordTriggerOccurrence",
    "AttackObjectIdentity",
    "AttackRecipient",
    "AttackRecipientKind",
    "AttackTransitionAssignment",
    "AttackTransitionError",
    "AttackTransitionEvent",
    "AttackTransitionParticipant",
    "AttackTransitionQuery",
    "build_attack_transition",
    "derive_attack_keyword_trigger_occurrences",
]
