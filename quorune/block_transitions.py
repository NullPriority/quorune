from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Protocol, Sequence

from .ability_fragments import (
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
)
from .continuous_effect_model import (
    ContinuousEffectDuration,
    ContinuousOperation,
    Layer,
)
from .continuous_effect_state import (
    ResolutionEffectSource,
    create_resolution_continuous_effect,
)
from .model import StackItem
from .util import stable_json


BLOCK_KEYWORD_TRIGGER_SEMANTIC_KEY = "builtin:block-keyword-trigger"


class BlockTransitionError(ValueError):
    """A declaration-time block transition is malformed or stale."""


def _identity(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise BlockTransitionError(f"{field} must be a nonempty string")
    return value


def _integer(value: Any, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise BlockTransitionError(
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
        raise BlockTransitionError(f"{field} has a closed field set")
    return value


@dataclass(frozen=True, slots=True)
class BlockObjectIdentity:
    object_id: str
    logical_object_id: str
    reference: str

    def __post_init__(self) -> None:
        _identity(self.object_id, field="Block object physical identity")
        _identity(
            self.logical_object_id,
            field="Block object logical identity",
        )
        _identity(self.reference, field="Block object reference")

    def to_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "reference": self.reference,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BlockObjectIdentity":
        data = _exact_mapping(
            value,
            {"object_id", "logical_object_id", "reference"},
            field="Block object identity",
        )
        return cls(
            object_id=data["object_id"],
            logical_object_id=data["logical_object_id"],
            reference=data["reference"],
        )


@dataclass(frozen=True, slots=True)
class BlockTransitionParticipant:
    object_id: str
    logical_object_id: str
    reference: str
    controller: str
    trigger_specs: tuple[CombatKeywordTriggerSpec, ...] = ()
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identity(self.object_id, field="Block participant physical identity")
        _identity(
            self.logical_object_id,
            field="Block participant logical identity",
        )
        _identity(self.reference, field="Block participant reference")
        _identity(self.controller, field="Block participant controller")
        specs = tuple(self.trigger_specs)
        if any(
            not isinstance(spec, CombatKeywordTriggerSpec) for spec in specs
        ):
            raise BlockTransitionError(
                "Block participant trigger specs must be typed fragments"
            )
        object.__setattr__(
            self,
            "trigger_specs",
            tuple(
                sorted(
                    specs,
                    key=lambda spec: stable_json(spec.to_dict()),
                )
            ),
        )
        raw_keywords = self.keywords
        if not isinstance(raw_keywords, tuple) or any(
            type(value) is not str or not value.strip()
            for value in raw_keywords
        ):
            raise BlockTransitionError(
                "Block participant keywords must be a tuple of nonempty strings"
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
            raise BlockTransitionError(
                "Block participant keywords must be distinct nonempty strings"
            )
        object.__setattr__(self, "keywords", keywords)

    @property
    def identity(self) -> BlockObjectIdentity:
        return BlockObjectIdentity(
            object_id=self.object_id,
            logical_object_id=self.logical_object_id,
            reference=self.reference,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            **self.identity.to_dict(),
            "controller": self.controller,
            "trigger_specs": [spec.to_dict() for spec in self.trigger_specs],
        }
        if self.keywords:
            result["keywords"] = list(self.keywords)
        return result

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "BlockTransitionParticipant":
        expected = {
            "object_id",
            "logical_object_id",
            "reference",
            "controller",
            "trigger_specs",
        }
        fields = set(value) if isinstance(value, Mapping) else set()
        if fields not in {frozenset(expected), frozenset({*expected, "keywords"})}:
            raise BlockTransitionError(
                "Block transition participant has a closed field set"
            )
        data = value
        raw_specs = data["trigger_specs"]
        if not isinstance(raw_specs, (list, tuple)):
            raise BlockTransitionError(
                "Block participant trigger_specs must be an array"
            )
        raw_keywords = data.get("keywords", ())
        if not isinstance(raw_keywords, (list, tuple)) or isinstance(
            raw_keywords, (str, bytes)
        ):
            raise BlockTransitionError(
                "Block participant keywords must be an array"
            )
        return cls(
            object_id=data["object_id"],
            logical_object_id=data["logical_object_id"],
            reference=data["reference"],
            controller=data["controller"],
            trigger_specs=tuple(
                CombatKeywordTriggerSpec.from_dict(spec)
                for spec in raw_specs
            ),
            keywords=tuple(raw_keywords),
        )


@dataclass(frozen=True, slots=True)
class BlockTransitionAssignment:
    attacker_object_id: str
    blocker_object_id: str

    def __post_init__(self) -> None:
        _identity(
            self.attacker_object_id,
            field="Block transition attacker identity",
        )
        _identity(
            self.blocker_object_id,
            field="Block transition blocker identity",
        )
        if self.attacker_object_id == self.blocker_object_id:
            raise BlockTransitionError("A creature cannot block itself")

    def to_dict(self) -> dict[str, str]:
        return {
            "attacker_object_id": self.attacker_object_id,
            "blocker_object_id": self.blocker_object_id,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "BlockTransitionAssignment":
        data = _exact_mapping(
            value,
            {"attacker_object_id", "blocker_object_id"},
            field="Block transition assignment",
        )
        return cls(**dict(data))


def _transition_payload(
    *,
    turn_sequence: int,
    priority_epoch: int,
    active_player: str,
    participants: Sequence[BlockTransitionParticipant],
    assignments: Sequence[BlockTransitionAssignment],
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
    return "block-transition:" + hashlib.sha256(
        stable_json(payload).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class BlockTransitionEvent:
    transition_id: str
    turn_sequence: int
    priority_epoch: int
    active_player: str
    participants: tuple[BlockTransitionParticipant, ...]
    assignments: tuple[BlockTransitionAssignment, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        _identity(self.transition_id, field="Block transition identity")
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise BlockTransitionError(
                "Unsupported block-transition schema version"
            )
        _integer(self.turn_sequence, field="Block transition turn sequence")
        _integer(self.priority_epoch, field="Block transition priority epoch")
        _identity(self.active_player, field="Block transition active player")
        participants = tuple(self.participants)
        assignments = tuple(self.assignments)
        if not participants or not assignments:
            raise BlockTransitionError(
                "A block transition requires participants and assignments"
            )
        if any(
            not isinstance(value, BlockTransitionParticipant)
            for value in participants
        ) or any(
            not isinstance(value, BlockTransitionAssignment)
            for value in assignments
        ):
            raise BlockTransitionError(
                "Block transitions require typed immutable values"
            )
        object.__setattr__(self, "participants", participants)
        object.__setattr__(self, "assignments", assignments)

        physical_ids = [value.object_id for value in participants]
        logical_ids = [value.logical_object_id for value in participants]
        references = [value.reference for value in participants]
        for values, label in (
            (physical_ids, "physical identities"),
            (logical_ids, "logical identities"),
            (references, "references"),
        ):
            if len(values) != len(set(values)):
                raise BlockTransitionError(
                    f"Block participant {label} must be unique"
                )
        pairs = [
            (value.attacker_object_id, value.blocker_object_id)
            for value in assignments
        ]
        if len(pairs) != len(set(pairs)):
            raise BlockTransitionError(
                "Block assignments must not contain duplicates"
            )
        blocker_ids = [value.blocker_object_id for value in assignments]
        if len(blocker_ids) != len(set(blocker_ids)):
            raise BlockTransitionError(
                "Ordinary block transitions require one attacker per blocker"
            )
        participant_set = set(physical_ids)
        relationship_set = {
            object_id for pair in pairs for object_id in pair
        }
        if participant_set != relationship_set:
            raise BlockTransitionError(
                "Every block participant requires one closed relationship"
            )
        if any(
            attacker not in participant_set or blocker not in participant_set
            for attacker, blocker in pairs
        ):
            raise BlockTransitionError(
                "Block assignments must be closed over their participants"
            )
        by_id = {value.object_id: value for value in participants}
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
                    by_id[value.blocker_object_id].reference,
                    value.attacker_object_id,
                    value.blocker_object_id,
                ),
            )
        )
        if (
            participants != canonical_participants
            or assignments != canonical_assignments
        ):
            raise BlockTransitionError(
                "Block transition values must use canonical public order"
            )
        if any(
            by_id[value.attacker_object_id].controller != self.active_player
            for value in assignments
        ):
            raise BlockTransitionError(
                "Every attacker must be controlled by the active player"
            )
        payload = _transition_payload(
            turn_sequence=self.turn_sequence,
            priority_epoch=self.priority_epoch,
            active_player=self.active_player,
            participants=participants,
            assignments=assignments,
        )
        if self.transition_id != _transition_id(payload):
            raise BlockTransitionError(
                "Block transition identity does not match its contents"
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
        participants: Sequence[BlockTransitionParticipant],
        assignments: Sequence[BlockTransitionAssignment],
    ) -> "BlockTransitionEvent":
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
    def from_dict(cls, value: Mapping[str, Any]) -> "BlockTransitionEvent":
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
            field="Block transition event",
        )
        raw_participants = data["participants"]
        raw_assignments = data["assignments"]
        if not isinstance(raw_participants, (list, tuple)) or not isinstance(
            raw_assignments, (list, tuple)
        ):
            raise BlockTransitionError(
                "Block transition participants and assignments must be arrays"
            )
        return cls(
            schema_version=data["schema_version"],
            transition_id=data["transition_id"],
            turn_sequence=data["turn_sequence"],
            priority_epoch=data["priority_epoch"],
            active_player=data["active_player"],
            participants=tuple(
                BlockTransitionParticipant.from_dict(item)
                for item in raw_participants
            ),
            assignments=tuple(
                BlockTransitionAssignment.from_dict(item)
                for item in raw_assignments
            ),
        )


class BlockTransitionQuery(Protocol):
    def turn_sequence(self) -> int: ...

    def priority_epoch(self) -> int: ...

    def active_player(self) -> str: ...

    def attacker_object_ids(self) -> Sequence[str]: ...

    def blocker_object_ids(self, attacker_object_id: str) -> Sequence[str]: ...

    def participant(self, object_id: str) -> BlockTransitionParticipant: ...


def build_block_transition(
    query: BlockTransitionQuery,
) -> BlockTransitionEvent | None:
    """Capture one canonical public transition after all blocks are declared."""

    attacker_ids = tuple(query.attacker_object_ids())
    if len(attacker_ids) != len(set(attacker_ids)) or any(
        not value for value in attacker_ids
    ):
        raise BlockTransitionError(
            "Block transition attackers must be unique and nonempty"
        )
    assignment_ids: list[tuple[str, str]] = []
    participant_ids: set[str] = set()
    participants_by_id: dict[str, BlockTransitionParticipant] = {}
    for attacker_id in attacker_ids:
        blocker_ids = tuple(query.blocker_object_ids(attacker_id))
        if len(blocker_ids) != len(set(blocker_ids)) or any(
            not value for value in blocker_ids
        ):
            raise BlockTransitionError(
                "Block transition blockers must be unique and nonempty"
            )
        if not blocker_ids:
            continue
        participant_ids.add(attacker_id)
        assignment_ids.extend(
            (attacker_id, blocker_id) for blocker_id in blocker_ids
        )
        participant_ids.update(blocker_ids)
    if not assignment_ids:
        return None
    for object_id in participant_ids:
        participant = query.participant(object_id)
        if participant.object_id != object_id:
            raise BlockTransitionError(
                "Block query participant identity changed while capturing"
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
                BlockTransitionAssignment(
                    attacker_object_id=attacker_id,
                    blocker_object_id=blocker_id,
                )
                for attacker_id, blocker_id in assignment_ids
            ),
            key=lambda value: (
                participants_by_id[value.attacker_object_id].reference,
                participants_by_id[value.blocker_object_id].reference,
                value.attacker_object_id,
                value.blocker_object_id,
            ),
        )
    )
    return BlockTransitionEvent.create(
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
    source: BlockObjectIdentity,
    affected: BlockObjectIdentity,
    amount: int,
    instance_index: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "transition_id": transition_id,
        "kind": kind.value,
        "controller": controller,
        "source": source.to_dict(),
        "affected": affected.to_dict(),
        "amount": amount,
        "instance_index": instance_index,
    }


def _occurrence_id(payload: Mapping[str, Any]) -> str:
    return "block-trigger:" + hashlib.sha256(
        stable_json(payload).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class BlockKeywordTriggerOccurrence:
    occurrence_id: str
    transition_id: str
    kind: CombatKeywordTriggerKind
    controller: str
    source: BlockObjectIdentity
    affected: BlockObjectIdentity
    amount: int
    instance_index: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        _identity(self.occurrence_id, field="Block trigger occurrence identity")
        _identity(self.transition_id, field="Block trigger transition identity")
        if self.schema_version != 1 or type(self.schema_version) is not int:
            raise BlockTransitionError(
                "Unsupported block-trigger occurrence schema version"
            )
        if not isinstance(self.kind, CombatKeywordTriggerKind):
            raise BlockTransitionError("Unsupported block-trigger kind")
        _identity(self.controller, field="Block trigger controller")
        if not isinstance(self.source, BlockObjectIdentity) or not isinstance(
            self.affected, BlockObjectIdentity
        ):
            raise BlockTransitionError(
                "Block triggers require typed source and affected identities"
            )
        _integer(self.amount, field="Block trigger amount", minimum=1)
        _integer(
            self.instance_index,
            field="Block trigger instance index",
        )
        if self.kind is CombatKeywordTriggerKind.FLANKING and self.amount != 1:
            raise BlockTransitionError("Each Flanking occurrence has amount 1")
        if (
            self.kind is CombatKeywordTriggerKind.BUSHIDO
            and self.source != self.affected
        ):
            raise BlockTransitionError("Bushido modifies its own source object")
        if (
            self.kind is CombatKeywordTriggerKind.FLANKING
            and self.source == self.affected
        ):
            raise BlockTransitionError("Flanking modifies the blocking creature")
        payload = _occurrence_payload(
            transition_id=self.transition_id,
            kind=self.kind,
            controller=self.controller,
            source=self.source,
            affected=self.affected,
            amount=self.amount,
            instance_index=self.instance_index,
        )
        if self.occurrence_id != _occurrence_id(payload):
            raise BlockTransitionError(
                "Block trigger occurrence identity does not match its contents"
            )

    @property
    def power_delta(self) -> int:
        return (
            -self.amount
            if self.kind is CombatKeywordTriggerKind.FLANKING
            else self.amount
        )

    @property
    def toughness_delta(self) -> int:
        return self.power_delta

    @property
    def label(self) -> str:
        if self.kind is CombatKeywordTriggerKind.FLANKING:
            return f"{self.source.reference} — Flanking"
        return f"{self.source.reference} — Bushido {self.amount}"

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
        source: BlockObjectIdentity,
        affected: BlockObjectIdentity,
        amount: int,
        instance_index: int,
    ) -> "BlockKeywordTriggerOccurrence":
        payload = _occurrence_payload(
            transition_id=transition_id,
            kind=kind,
            controller=controller,
            source=source,
            affected=affected,
            amount=amount,
            instance_index=instance_index,
        )
        return cls(
            occurrence_id=_occurrence_id(payload),
            transition_id=transition_id,
            kind=kind,
            controller=controller,
            source=source,
            affected=affected,
            amount=amount,
            instance_index=instance_index,
        )

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "BlockKeywordTriggerOccurrence":
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
            field="Block trigger occurrence",
        )
        try:
            kind = CombatKeywordTriggerKind(data["kind"])
        except (TypeError, ValueError) as exc:
            raise BlockTransitionError("Unsupported block-trigger kind") from exc
        return cls(
            schema_version=data["schema_version"],
            occurrence_id=data["occurrence_id"],
            transition_id=data["transition_id"],
            kind=kind,
            controller=data["controller"],
            source=BlockObjectIdentity.from_dict(data["source"]),
            affected=BlockObjectIdentity.from_dict(data["affected"]),
            amount=data["amount"],
            instance_index=data["instance_index"],
        )


def derive_block_keyword_trigger_occurrences(
    event: BlockTransitionEvent,
) -> tuple[BlockKeywordTriggerOccurrence, ...]:
    """Derive CR 702.25/702.45 occurrences from one sealed transition."""

    if not isinstance(event, BlockTransitionEvent):
        raise BlockTransitionError(
            "Block keyword triggers require a typed transition event"
        )
    participants = {value.object_id: value for value in event.participants}
    drafts: list[
        tuple[
            CombatKeywordTriggerKind,
            BlockTransitionParticipant,
            BlockTransitionParticipant,
            int,
            int,
        ]
    ] = []
    blocked_attackers: set[str] = set()
    blocking_creatures: set[str] = set()
    for assignment in event.assignments:
        attacker = participants[assignment.attacker_object_id]
        blocker = participants[assignment.blocker_object_id]
        blocked_attackers.add(attacker.object_id)
        blocking_creatures.add(blocker.object_id)
        blocker_has_flanking = any(
            spec.kind is CombatKeywordTriggerKind.FLANKING
            for spec in blocker.trigger_specs
        )
        if blocker_has_flanking:
            continue
        for index, spec in enumerate(
            value
            for value in attacker.trigger_specs
            if value.kind is CombatKeywordTriggerKind.FLANKING
        ):
            drafts.append((spec.kind, attacker, blocker, spec.amount, index))

    bushido_sources = blocked_attackers | blocking_creatures
    for source in event.participants:
        if source.object_id not in bushido_sources:
            continue
        for index, spec in enumerate(
            value
            for value in source.trigger_specs
            if value.kind is CombatKeywordTriggerKind.BUSHIDO
        ):
            drafts.append((spec.kind, source, source, spec.amount, index))

    drafts.sort(
        key=lambda value: (
            value[1].reference,
            value[0].value,
            value[2].reference,
            value[4],
            value[3],
        )
    )
    return tuple(
        BlockKeywordTriggerOccurrence.create(
            transition_id=event.transition_id,
            kind=kind,
            controller=source.controller,
            source=source.identity,
            affected=affected.identity,
            amount=amount,
            instance_index=index,
        )
        for kind, source, affected, amount, index in drafts
    )


def block_keyword_trigger_stack_item(
    occurrence: BlockKeywordTriggerOccurrence,
    *,
    ref: str,
    stack_id: str,
    visibility: Sequence[str],
) -> StackItem:
    if not isinstance(occurrence, BlockKeywordTriggerOccurrence):
        raise BlockTransitionError(
            "A block-trigger stack item requires a typed occurrence"
        )
    _identity(ref, field="Block-trigger stack reference")
    _identity(stack_id, field="Block-trigger stack identity")
    return StackItem(
        stack_id=stack_id,
        ref=ref,
        kind="triggered_ability",
        controller=occurrence.controller,
        label=occurrence.label,
        source_object_id=occurrence.source.object_id,
        semantic_key=BLOCK_KEYWORD_TRIGGER_SEMANTIC_KEY,
        visibility=list(visibility),
        context={
            "event": "combat.block_transition",
            "block_keyword_trigger": occurrence.to_dict(),
        },
        referred_object_ids=list(
            dict.fromkeys(
                (
                    occurrence.source.object_id,
                    occurrence.affected.object_id,
                )
            )
        ),
    )


class BlockKeywordResolutionHost(Protocol):
    state: Any

    def _next_ref(self, prefix: str) -> str: ...

    def _next_zone_timestamp(self) -> int: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _log(self, *args: Any, **kwargs: Any) -> None: ...


def resolve_block_keyword_trigger(
    host: BlockKeywordResolutionHost,
    occurrence: BlockKeywordTriggerOccurrence,
    *,
    stack_ref: str,
) -> bool:
    """Resolve against the exact affected logical object, if it still exists."""

    if not isinstance(occurrence, BlockKeywordTriggerOccurrence):
        raise BlockTransitionError(
            "Block-trigger resolution requires a typed occurrence"
        )
    _identity(stack_ref, field="Resolving block-trigger stack reference")
    card = host.state.cards.get(occurrence.affected.object_id)
    still_present = bool(
        card is not None
        and card.zone == "battlefield"
        and card.logical_object_id == occurrence.affected.logical_object_id
    )
    if still_present:
        effect = create_resolution_continuous_effect(
            host,
            source=ResolutionEffectSource(
                stack_ref=stack_ref,
                object_id=occurrence.source.object_id,
                logical_object_id=occurrence.source.logical_object_id,
                card_ref=occurrence.source.reference,
            ),
            targets=(card,),
            layer=Layer.POWER_TOUGHNESS,
            sublayer="7c",
            operations=(
                ContinuousOperation(
                    "modify_power_toughness",
                    [occurrence.power_delta, occurrence.toughness_delta],
                ),
            ),
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
        )
        if effect is None:
            raise BlockTransitionError(
                "Block-trigger resolution requires the continuous-effect journal"
            )
    host._log(
        occurrence.controller,
        "combat.block_keyword.resolve",
        (
            f"Resolved {occurrence.label}."
            if still_present
            else f"Resolved {occurrence.label} with no affected object."
        ),
        {
            "occurrence": occurrence.occurrence_id,
            "transition": occurrence.transition_id,
            "kind": occurrence.kind.value,
            "source": occurrence.source.reference,
            "affected": occurrence.affected.reference,
            "amount": occurrence.amount,
            "applied": still_present,
        },
        importance=2,
        changed_objects=(
            [occurrence.affected.object_id] if still_present else []
        ),
    )
    return still_present


__all__ = [
    "BLOCK_KEYWORD_TRIGGER_SEMANTIC_KEY",
    "BlockKeywordTriggerOccurrence",
    "BlockObjectIdentity",
    "BlockTransitionAssignment",
    "BlockTransitionError",
    "BlockTransitionEvent",
    "BlockTransitionParticipant",
    "BlockTransitionQuery",
    "block_keyword_trigger_stack_item",
    "build_block_transition",
    "derive_block_keyword_trigger_occurrences",
    "resolve_block_keyword_trigger",
]
