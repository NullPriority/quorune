from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .combat_damage_snapshot import (
    CombatDamageParticipant,
    CombatDamageSnapshot,
)
from .combat_damage_trample import trample_assignment_error
from .combat_damage_values import (
    assignment_identity as _identity,
    CombatDamageAssignmentError,
    CreatureDamageState,
    DamageAssignment,
    exact_assignment_integer as _exact_integer,
    TrampleDamageSpec,
    AsUnblockedDamageSpec,
)


@dataclass(frozen=True, slots=True)
class CombatDamageSourceSpec:
    source: str
    controller: str
    logical_object_id: str
    power: int
    targets: tuple[str, ...]

    def __post_init__(self) -> None:
        _identity(self.source, label="Damage source")
        _identity(self.controller, label="Damage source controller")
        _identity(
            self.logical_object_id,
            label="Damage source logical identity",
        )
        _exact_integer(self.power, label="Damage power")
        if isinstance(self.targets, (str, bytes)):
            raise CombatDamageAssignmentError(
                "Damage targets must be a collection of references"
            )
        targets = tuple(self.targets)
        object.__setattr__(self, "targets", targets)
        if not all(isinstance(value, str) and value for value in targets):
            raise CombatDamageAssignmentError(
                "Damage targets must be nonempty references"
            )
        if len(targets) != len(set(targets)):
            raise CombatDamageAssignmentError(
                "Damage targets must be unique nonempty references"
            )


@dataclass(frozen=True, slots=True)
class CombatDamageAssignmentProposal:
    damage_step_id: str
    actor: str
    sources: tuple[CombatDamageSourceSpec, ...]
    attacking_sources: frozenset[str]
    deathtouch_sources: frozenset[str]
    trample_sources: tuple[TrampleDamageSpec, ...]
    as_unblocked_sources: tuple[AsUnblockedDamageSpec, ...] = ()
    mandatory_as_unblocked_sources: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _identity(self.damage_step_id, label="Damage-step identity")
        _identity(self.actor, label="Assignment actor")
        sources = tuple(self.sources)
        trample_sources = tuple(self.trample_sources)
        attacking_sources = frozenset(self.attacking_sources)
        deathtouch_sources = frozenset(self.deathtouch_sources)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "trample_sources", trample_sources)
        choices=tuple(self.as_unblocked_sources)
        object.__setattr__(self,'as_unblocked_sources',choices)
        if any(not isinstance(value,AsUnblockedDamageSpec) for value in choices):raise CombatDamageAssignmentError('As-unblocked choices must be typed')
        mandatory=frozenset(self.mandatory_as_unblocked_sources);object.__setattr__(self,'mandatory_as_unblocked_sources',mandatory)
        object.__setattr__(self, "attacking_sources", attacking_sources)
        object.__setattr__(self, "deathtouch_sources", deathtouch_sources)
        if not all(isinstance(value, CombatDamageSourceSpec) for value in sources):
            raise CombatDamageAssignmentError(
                "Combat damage sources must be typed specifications"
            )
        source_refs = [source.source for source in sources]
        if len(source_refs) != len(set(source_refs)):
            raise CombatDamageAssignmentError(
                "Combat damage sources must be unique"
            )
        if any(source.controller != self.actor for source in sources):
            raise CombatDamageAssignmentError(
                "Every combat damage source must belong to the proposal actor"
            )
        logical_ids = [source.logical_object_id for source in sources]
        if len(logical_ids) != len(set(logical_ids)):
            raise CombatDamageAssignmentError(
                "Combat damage source logical identities must be unique"
            )
        source_set = set(source_refs)
        if not attacking_sources <= source_set:
            raise CombatDamageAssignmentError(
                "Attacking damage sources must be proposal sources"
            )
        if not mandatory<=attacking_sources:raise CombatDamageAssignmentError('Mandatory as-unblocked rules require current attacking sources')
        if not deathtouch_sources <= attacking_sources:
            raise CombatDamageAssignmentError(
                "Deathtouch damage sources must be attacking sources"
            )
        tramplers = [source.attacker for source in trample_sources]
        if (
            len(tramplers) != len(set(tramplers))
            or not set(tramplers) <= attacking_sources
        ):
            raise CombatDamageAssignmentError(
                "Trample sources must be unique current attacking sources"
            )
        by_source = {source.source: source for source in sources}
        if len({value.attacker for value in choices})!=len(choices) or any(value.attacker not in attacking_sources or value.recipient not in by_source[value.attacker].targets for value in choices):raise CombatDamageAssignmentError('As-unblocked choices require unique current attackers and legal recipients')
        for trample in trample_sources:
            source_targets = by_source[trample.attacker].targets
            legal_targets = set(source_targets)
            if trample.spill_target not in legal_targets:
                raise CombatDamageAssignmentError(
                    "Trample spill targets must be legal source recipients"
                )
            blocker_refs = tuple(
                reference for reference, _state in trample.blockers
            )
            if blocker_refs != tuple(
                target
                for target in source_targets
                if target != trample.spill_target
            ):
                raise CombatDamageAssignmentError(
                    "Trample blockers must exactly match the source's legal "
                    "nonspill recipients"
                )

    @property
    def proposal_id(self) -> str:
        payload = {
            "actor": self.actor,
            "damage_step_id": self.damage_step_id,
            "sources": [
                {
                    "source": source.source,
                    "logical_object_id": source.logical_object_id,
                    "power": source.power,
                    "targets": list(source.targets),
                }
                for source in self.sources
            ],
        }
        if self.as_unblocked_sources:payload['as_unblocked']=[{'source':value.attacker,'recipient':value.recipient} for value in self.as_unblocked_sources]
        if self.mandatory_as_unblocked_sources:payload['mandatory_as_unblocked']=sorted(self.mandatory_as_unblocked_sources)
        digest = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"combat-assignment:{digest}"

    def projected_options(self) -> dict[str, dict[str, Any]]:
        options = {
            source.source: {
                "power": source.power,
                "targets": list(source.targets),
            }
            for source in self.sources
        }
        for permission in self.as_unblocked_sources:
            option=options[permission.attacker];option['as_unblocked_recipient']=permission.recipient
            if option['targets']==[permission.recipient] and permission.attacker not in {value.attacker for value in self.trample_sources}:
                option['allowed_totals']=[0,option['power']]
        return options

    def automatic_assignments(self) -> tuple[DamageAssignment, ...] | None:
        """Return forced assignments, or None when a source must divide."""

        if any(len(source.targets) > 1 for source in self.sources) or any(source.power>0 and any(permission.attacker==source.source for permission in self.as_unblocked_sources) and source.source not in {value.attacker for value in self.trample_sources} for source in self.sources):
            return None
        return tuple(
            DamageAssignment(source.source, source.targets[0], source.power)
            for source in self.sources
            if source.power > 0 and source.targets
        )

    def validate(
        self, submitted: Sequence[Mapping[str, Any]]
    ) -> tuple[DamageAssignment, ...]:
        if (
            not isinstance(submitted, Sequence)
            or isinstance(submitted, (str, bytes, Mapping))
        ):
            raise CombatDamageAssignmentError(
                "Combat-damage assignments must be an array"
            )
        source_map = {source.source: source for source in self.sources}
        totals: dict[str, int] = {}
        parsed: dict[tuple[str, str], DamageAssignment] = {}
        for raw in submitted:
            assignment = _parse_assignment(raw, source_map)
            pair = (assignment.source, assignment.target)
            if pair in parsed:
                raise CombatDamageAssignmentError(
                    "A combat-damage source/target pair may appear only once"
                )
            parsed[pair] = assignment
            totals[assignment.source] = (
                totals.get(assignment.source, 0) + assignment.amount
            )

        for source in self.sources:
            no_blockers_choice=any(permission.attacker==source.source and source.targets==(permission.recipient,) for permission in self.as_unblocked_sources) and source.source not in {value.attacker for value in self.trample_sources}
            required = source.power if source.targets else 0
            assigned = totals.get(source.source, 0)
            if assigned != required and not (no_blockers_choice and assigned==0):
                raise CombatDamageAssignmentError(
                    f"{source.source} must assign exactly {required} combat "
                    f"damage, not {assigned}"
                )

        assignments = tuple(
            assignment
            for source in self.sources
            for target in source.targets
            if (assignment := parsed.get((source.source, target))) is not None
            and assignment.amount > 0
        )
        bypassed=set()
        tramplers={value.attacker for value in self.trample_sources}
        for permission in self.as_unblocked_sources:
            source=source_map[permission.attacker]
            recipient_amount=next((row.amount for row in assignments if row.source==permission.attacker and row.target==permission.recipient),0)
            if recipient_amount==0:continue
            all_to_recipient=recipient_amount==source.power and not any(row.source==permission.attacker and row.target!=permission.recipient and row.amount>0 for row in assignments)
            if all_to_recipient:bypassed.add(permission.attacker)
            elif permission.attacker not in tramplers:raise CombatDamageAssignmentError('As-unblocked assignment must send all damage to the attacked recipient')
        for trample in self.trample_sources:
            if trample.attacker in bypassed:continue
            error = trample_assignment_error(
                attacker_ref=trample.attacker,
                spill_target=trample.spill_target,
                blockers=trample.blockers,
                assignments=assignments,
                attacking_source_refs=self.attacking_sources,
                deathtouch_source_refs=self.deathtouch_sources,
            )
            if error is not None:
                raise CombatDamageAssignmentError(error)
        return assignments


def build_combat_damage_assignment_proposal(
    *,
    seat: str,
    snapshot: CombatDamageSnapshot,
) -> CombatDamageAssignmentProposal:
    """Lower one immutable CR 510 snapshot into the seat's canonical proposal."""

    _identity(seat, label="Assignment seat")
    if not isinstance(snapshot, CombatDamageSnapshot):
        raise CombatDamageAssignmentError(
            "Combat assignment requires a typed immutable snapshot"
        )
    participants = {item.object_id: item for item in snapshot.participants}
    attacks = {item.source_object_id: item for item in snapshot.attacks}
    blocks_by_attacker: dict[str, tuple[str, ...]] = {
        attacker_id: tuple(
            relation.blocker_object_id
            for relation in snapshot.blocks
            if relation.attacker_object_id == attacker_id
        )
        for attacker_id in attacks
    }
    blocked_attackers_by_blocker: dict[str, tuple[str, ...]] = {
        participant.object_id: tuple(
            relation.attacker_object_id
            for relation in snapshot.blocks
            if relation.blocker_object_id == participant.object_id
        )
        for participant in snapshot.participants
    }

    sources: list[CombatDamageSourceSpec] = []
    attacking_sources: set[str] = set()
    deathtouch_sources: set[str] = set()
    trample_sources: list[TrampleDamageSpec] = []
    as_unblocked_sources: list[AsUnblockedDamageSpec] = []
    mandatory_as_unblocked_sources: set[str] = set()
    for participant in snapshot.participants:
        if participant.controller != seat or not participant.assigns_damage:
            continue
        targets: tuple[str, ...]
        attack = attacks.get(participant.object_id)
        if attack is not None:
            attacking_sources.add(participant.reference)
            if "deathtouch" in participant.keywords:
                deathtouch_sources.add(participant.reference)
            blocker_ids = blocks_by_attacker[participant.object_id]
            blocker_refs = tuple(participants[value].reference for value in blocker_ids)
            recipient = attack.recipient
            if participant.must_assign_as_unblocked:
                targets=(recipient.reference,) if recipient.legal else ()
                mandatory_as_unblocked_sources.add(participant.reference)
            elif participant.object_id in snapshot.was_blocked:
                targets = blocker_refs
                if participant.assign_as_unblocked and recipient.legal:
                    targets=(*targets,recipient.reference)
                    as_unblocked_sources.append(AsUnblockedDamageSpec(participant.reference,recipient.reference))
                if "trample" in participant.keywords and recipient.legal:
                    if recipient.reference not in targets:targets = (*targets, recipient.reference)
                    trample_sources.append(
                        TrampleDamageSpec(
                            attacker=participant.reference,
                            spill_target=recipient.reference,
                            blockers=tuple(
                                (
                                    participants[blocker_id].reference,
                                    CreatureDamageState(
                                        toughness=participants[blocker_id].toughness,
                                        marked_damage=participants[
                                            blocker_id
                                        ].marked_damage,
                                    ),
                                )
                                for blocker_id in blocker_ids
                            ),
                        )
                    )
            else:
                targets = (recipient.reference,) if recipient.legal else ()
        elif blocked_attackers_by_blocker[participant.object_id]:
            targets = tuple(
                participants[attacker_id].reference
                for attacker_id in blocked_attackers_by_blocker[
                    participant.object_id
                ]
            )
        else:
            raise CombatDamageAssignmentError(
                f"Combat participant {participant.reference} has no closed relationship"
            )
        sources.append(
            CombatDamageSourceSpec(
                source=participant.reference,
                controller=participant.controller,
                logical_object_id=participant.logical_object_id,
                power=max(0, participant.toughness if participant.assigns_using_toughness else participant.power),
                targets=targets,
            )
        )
    return CombatDamageAssignmentProposal(
        damage_step_id=snapshot.damage_step_id,
        actor=seat,
        sources=tuple(sources),
        attacking_sources=frozenset(attacking_sources),
        deathtouch_sources=frozenset(deathtouch_sources),
        trample_sources=tuple(trample_sources),
        as_unblocked_sources=tuple(as_unblocked_sources),
        mandatory_as_unblocked_sources=frozenset(mandatory_as_unblocked_sources),
    )


def _parse_assignment(
    raw: Mapping[str, Any],
    source_map: Mapping[str, CombatDamageSourceSpec],
) -> DamageAssignment:
    if not isinstance(raw, Mapping):
        raise CombatDamageAssignmentError(
            "Each combat-damage assignment must be an object"
        )
    if set(raw) != {"source", "target", "amount"}:
        raise CombatDamageAssignmentError(
            "Combat-damage assignment requires exactly source, target, and amount"
        )
    if not isinstance(raw["source"], str) or not raw["source"]:
        raise CombatDamageAssignmentError(
            "Combat-damage assignment source must be a nonempty string"
        )
    source_ref = raw["source"]
    source = source_map.get(source_ref)
    if source is None:
        raise CombatDamageAssignmentError(
            f"{source_ref} is not assigning combat damage"
        )
    if source.power <= 0:
        raise CombatDamageAssignmentError(
            f"{source_ref} does not assign combat damage because its power is 0 or less"
        )
    if not isinstance(raw["target"], str) or not raw["target"]:
        raise CombatDamageAssignmentError(
            "Combat-damage assignment target must be a nonempty string"
        )
    target_ref = raw["target"]
    if target_ref not in source.targets:
        raise CombatDamageAssignmentError(
            f"{target_ref} is an illegal combat-damage target for {source_ref}"
        )
    value = raw["amount"]
    if not isinstance(value, int) or isinstance(value, bool):
        raise CombatDamageAssignmentError("Combat damage must be an exact integer")
    if value < 0:
        raise CombatDamageAssignmentError("Damage cannot be negative")
    return DamageAssignment(source_ref, target_ref, value)


__all__ = [
    "build_combat_damage_assignment_proposal",
    "CombatDamageAssignmentError",
    "CombatDamageParticipant",
    "CombatDamageAssignmentProposal",
    "CombatDamageSourceSpec",
    "CreatureDamageState",
    "DamageAssignment",
    "TrampleDamageSpec",
    "trample_assignment_error",
]
