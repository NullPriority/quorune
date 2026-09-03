from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Protocol, Sequence

from .damage import DamageError, recipient_snapshot, source_snapshot
from .damage_modifier_state import (
    ChosenDamageSource,
    DamageAftermathRecipient,
    DamageModifierDuration,
    DamageModifierError,
    DamagePreventionScope,
    DamagePreventionShield,
    DamageSubject,
    DealDamagePreventionAftermath,
    GainLifePreventionAftermath,
    PlaceCountersPreventionAftermath,
    PreventionDamageKind,
    PreventionMode,
    PreventionRecipientKind,
)
from .object_query import (
    ObjectQueryError,
    object_matches_query,
    object_query_result,
    ObjectQuerySpec,
    validate_chosen_damage_source_predicate,
)
from .prevention_triggers import (
    DealDamagePreventionTrigger,
    DrawCardsPreventionTrigger,
    PlaceCountersPreventionTrigger,
    PreventionTriggeredAbility,
    PreventionTriggerError,
)
from .replacement.immutable import FrozenMap, thaw_value
from .util import stable_json


class DamagePreventionCreationError(ValueError):
    """A prevention resource could not be planned or committed exactly."""


class DamagePreventionCreationHost(Protocol):
    state: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


@dataclass(frozen=True, slots=True)
class PreventionSubjectAllocation:
    subject_ref: str
    amount: int | None

    def __post_init__(self) -> None:
        if not str(self.subject_ref or ""):
            raise DamagePreventionCreationError(
                "Prevention allocations require a subject"
            )
        if self.amount is not None and (
            type(self.amount) is not int or self.amount < 1
        ):
            raise DamagePreventionCreationError(
                "Prevention allocations require a positive integer amount"
            )


@dataclass(frozen=True, slots=True)
class GainLifeAftermathRequest:
    player: str
    per_prevented: int = 0
    fixed_amount: int = 0

    def __post_init__(self) -> None:
        # Reuse the persistent model's closed arithmetic validation.
        GainLifePreventionAftermath(
            player=self.player,
            per_prevented=self.per_prevented,
            fixed_amount=self.fixed_amount,
        )


@dataclass(frozen=True, slots=True)
class PlaceCountersAftermathRequest:
    counter_name: str
    placing_player: str
    per_prevented: int = 0
    fixed_amount: int = 0
    subject_ref: str | None = None

    def __post_init__(self) -> None:
        if not " ".join(str(self.counter_name).casefold().split()):
            raise DamagePreventionCreationError(
                "Counter aftermath requires a counter name"
            )
        if not str(self.placing_player or ""):
            raise DamagePreventionCreationError(
                "Counter aftermath requires a placing player"
            )
        if self.subject_ref is not None and not str(self.subject_ref):
            raise DamagePreventionCreationError(
                "Counter aftermath subject references cannot be empty"
            )
        if (
            type(self.per_prevented) is not int
            or self.per_prevented < 0
            or type(self.fixed_amount) is not int
            or self.fixed_amount < 0
            or not (self.per_prevented or self.fixed_amount)
        ):
            raise DamagePreventionCreationError(
                "Counter aftermath requires a positive fixed or scaled amount"
            )


@dataclass(frozen=True, slots=True)
class DealDamageAftermathRequest:
    source_ref: str
    per_prevented: int = 0
    fixed_amount: int = 0
    recipient_ref: str | None = None
    recipient_kind: str = "fixed"

    def __post_init__(self) -> None:
        if not str(self.source_ref or ""):
            raise DamagePreventionCreationError(
                "Prevention damage aftermath requires a source"
            )
        if self.recipient_kind == "fixed":
            if not str(self.recipient_ref or ""):
                raise DamagePreventionCreationError(
                    "Fixed prevention damage requires a recipient"
                )
        elif self.recipient_kind == "prevented_source_controller":
            if self.recipient_ref is not None:
                raise DamagePreventionCreationError(
                    "A prevented-source controller recipient cannot be fixed"
                )
        else:
            raise DamagePreventionCreationError(
                "Prevention damage recipient kind is unsupported"
            )
        if (
            type(self.per_prevented) is not int
            or self.per_prevented < 0
            or type(self.fixed_amount) is not int
            or self.fixed_amount < 0
            or not (self.per_prevented or self.fixed_amount)
        ):
            raise DamagePreventionCreationError(
                "Prevention damage requires a positive fixed or scaled amount"
            )


PreventionAftermathRequest = (
    GainLifeAftermathRequest
    | PlaceCountersAftermathRequest
    | DealDamageAftermathRequest
)


@dataclass(frozen=True, slots=True)
class DrawCardsTriggerRequest:
    player: str
    per_prevented: int = 0
    fixed_amount: int = 0
    private: bool = True

    def __post_init__(self) -> None:
        try:
            DrawCardsPreventionTrigger(
                player=self.player,
                per_prevented=self.per_prevented,
                fixed_amount=self.fixed_amount,
                private=self.private,
            )
        except PreventionTriggerError as exc:
            raise DamagePreventionCreationError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class DealDamageTriggerRequest:
    source_ref: str
    recipient_kind: str
    per_prevented: int = 0
    fixed_amount: int = 0

    def __post_init__(self) -> None:
        if not self.source_ref or self.recipient_kind not in {
            "prevented_source_controller",
            "selected_target",
        }:
            raise DamagePreventionCreationError(
                "Prevention-trigger damage requires a source and supported recipient"
            )
        if (
            type(self.per_prevented) is not int
            or self.per_prevented < 0
            or type(self.fixed_amount) is not int
            or self.fixed_amount < 0
            or not (self.per_prevented or self.fixed_amount)
        ):
            raise DamagePreventionCreationError(
                "Prevention-trigger damage requires a positive fixed or scaled amount"
            )


@dataclass(frozen=True, slots=True)
class PlaceCountersTriggerRequest:
    subject_ref: str
    counter_name: str
    placing_player: str
    per_prevented: int = 0
    fixed_amount: int = 0

    def __post_init__(self) -> None:
        try:
            PlaceCountersPreventionTrigger(
                subject_ref=self.subject_ref,
                counter_name=self.counter_name,
                placing_player=self.placing_player,
                per_prevented=self.per_prevented,
                fixed_amount=self.fixed_amount,
            )
        except PreventionTriggerError as exc:
            raise DamagePreventionCreationError(str(exc)) from exc


PreventionTriggerResultRequest = (
    DrawCardsTriggerRequest
    | DealDamageTriggerRequest
    | PlaceCountersTriggerRequest
)


@dataclass(frozen=True, slots=True)
class PreventionTriggeredAbilityRequest:
    source_ref: str
    label: str
    results: tuple[PreventionTriggerResultRequest, ...]
    target_schema: FrozenMap = FrozenMap()

    def __post_init__(self) -> None:
        values = tuple(self.results)
        if not self.source_ref or not self.label or not values:
            raise DamagePreventionCreationError(
                "Prevention-trigger creation requires source, label, and results"
            )
        if any(
            not isinstance(
                value,
                (
                    DrawCardsTriggerRequest,
                    DealDamageTriggerRequest,
                    PlaceCountersTriggerRequest,
                ),
            )
            for value in values
        ):
            raise DamagePreventionCreationError(
                "Prevention-trigger creation results must be typed"
            )
        object.__setattr__(self, "results", values)
        if not isinstance(self.target_schema, FrozenMap):
            object.__setattr__(self, "target_schema", FrozenMap(self.target_schema))
        needs_target = any(
            isinstance(value, DealDamageTriggerRequest)
            and value.recipient_kind == "selected_target"
            for value in values
        )
        if needs_target != bool(self.target_schema):
            raise DamagePreventionCreationError(
                "Targeted prevention-trigger creation requires exactly one target schema"
            )


@dataclass(frozen=True, slots=True)
class PreventionShieldCreationRequest:
    source_id: str
    controller: str
    mode: PreventionMode
    duration: DamageModifierDuration
    subjects: tuple[PreventionSubjectAllocation, ...]
    damage_kind: PreventionDamageKind = PreventionDamageKind.ANY
    recipient_kind: PreventionRecipientKind = PreventionRecipientKind.ANY
    scope: DamagePreventionScope = DamagePreventionScope()
    chosen_source_ref: str | None = None
    source_predicate: ObjectQuerySpec = ObjectQuerySpec()
    label: str = ""
    application_group_id: str | None = None
    aftermath: tuple[PreventionAftermathRequest, ...] = ()
    triggered_ability: PreventionTriggeredAbilityRequest | None = None

    def __post_init__(self) -> None:
        if not str(self.source_id or "") or not str(self.controller or ""):
            raise DamagePreventionCreationError(
                "Prevention creation requires source and controller identity"
            )
        if not isinstance(self.mode, PreventionMode) or not isinstance(
            self.duration, DamageModifierDuration
        ):
            raise DamagePreventionCreationError(
                "Prevention creation requires typed mode and duration"
            )
        if not isinstance(self.damage_kind, PreventionDamageKind) or not isinstance(
            self.recipient_kind, PreventionRecipientKind
        ):
            raise DamagePreventionCreationError(
                "Prevention creation requires typed damage and recipient scope"
            )
        if not isinstance(self.scope, DamagePreventionScope):
            raise DamagePreventionCreationError(
                "Prevention creation requires a typed applicability scope"
            )
        subjects = tuple(self.subjects)
        if not subjects or any(
            not isinstance(value, PreventionSubjectAllocation)
            for value in subjects
        ):
            raise DamagePreventionCreationError(
                "Prevention creation requires typed subject allocations"
            )
        refs = tuple(value.subject_ref for value in subjects)
        if len(refs) != len(set(refs)):
            raise DamagePreventionCreationError(
                "A prevention creation cannot repeat a subject"
            )
        if self.mode == PreventionMode.AMOUNT:
            if any(value.amount is None for value in subjects):
                raise DamagePreventionCreationError(
                    "Amount shields require an amount for every subject"
                )
        elif any(value.amount is not None for value in subjects):
            raise DamagePreventionCreationError(
                "Only amount shields accept subject allocations"
            )
        object.__setattr__(self, "subjects", subjects)
        if not isinstance(self.source_predicate, ObjectQuerySpec):
            raise DamagePreventionCreationError(
                "Prevention creation requires a typed source predicate"
            )
        if self.application_group_id is not None and (
            type(self.application_group_id) is not str
            or not self.application_group_id
        ):
            raise DamagePreventionCreationError(
                "Prevention creation application group must be nonempty or null"
            )
        aftermath = tuple(self.aftermath)
        if any(
            not isinstance(
                value,
                (
                    GainLifeAftermathRequest,
                    PlaceCountersAftermathRequest,
                    DealDamageAftermathRequest,
                ),
            )
            for value in aftermath
        ):
            raise DamagePreventionCreationError(
                "Prevention aftermath requests must be typed"
            )
        object.__setattr__(self, "aftermath", aftermath)
        if self.triggered_ability is not None and not isinstance(
            self.triggered_ability, PreventionTriggeredAbilityRequest
        ):
            raise DamagePreventionCreationError(
                "Prevention triggered-ability request must be typed"
            )


@dataclass(frozen=True, slots=True)
class DamagePreventionCreationPlan:
    state_fingerprint: str
    shields: tuple[DamagePreventionShield, ...]


def _damage_subject(host: DamagePreventionCreationHost, ref: str, actor: str) -> DamageSubject:
    if ref == "*":
        return DamageSubject(ref="*", kind="any", controller=actor)
    snapshot = recipient_snapshot(host, ref, actor=actor)
    return DamageSubject(
        ref=snapshot.ref,
        kind=snapshot.kind,
        controller=snapshot.controller,
        object_id=snapshot.object_id,
        logical_object_id=snapshot.logical_object_id,
        owner=snapshot.owner,
    )


def pin_chosen_damage_source(
    host: DamagePreventionCreationHost,
    *,
    source_ref: str | None,
    controller: str,
    predicate: ObjectQuerySpec = ObjectQuerySpec(),
) -> ChosenDamageSource | None:
    """Pin one legal source choice to physical identity and current LKI."""

    ref = source_ref
    if ref is None:
        return None
    if not isinstance(predicate, ObjectQuerySpec):
        raise DamagePreventionCreationError(
            "Chosen damage sources require a typed predicate"
        )
    try:
        validate_chosen_damage_source_predicate(predicate)
    except ObjectQueryError as exc:
        raise DamagePreventionCreationError(str(exc)) from exc
    snapshot = source_snapshot(host, ref, controller=controller)
    if snapshot.object_id.startswith("unrepresented:"):
        raise DamagePreventionCreationError(
            "A chosen damage source requires authoritative physical identity"
        )
    card = host.state.cards.get(snapshot.object_id)
    if card is None:
        raise DamagePreventionCreationError(
            "The chosen damage source is no longer represented"
        )
    data = host._effective_card_data(card)
    types, subtypes, supertypes = host._type_parts(
        str(data.get("type_line") or "")
    )
    row = object_query_result(
        card,
        data,
        type_parts=(types, subtypes, supertypes),
        known_to_actor=True,
        attached_to_ref=None,
    )
    if not object_matches_query(row, predicate):
        raise DamagePreventionCreationError(
            "The chosen damage source no longer has the required characteristics"
        )
    identity_keys = [snapshot.identity_key]
    permanent_types = {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "planeswalker",
    }
    if snapshot.zone == "stack" and permanent_types.intersection(types):
        identity_keys.append(
            f"{snapshot.logical_object_id}|battlefield"
        )
    return ChosenDamageSource(
        ref=snapshot.ref,
        object_id=snapshot.object_id,
        predicate=predicate,
        snapshot_version=3,
        logical_object_id=snapshot.logical_object_id,
        oracle_id=snapshot.oracle_id,
        printed_name=str(card.printed_name),
        controller=snapshot.controller,
        owner=snapshot.owner,
        zone=str(card.zone),
        types=row.types,
        subtypes=row.subtypes,
        supertypes=row.supertypes,
        colors=row.colors,
        keywords=row.keywords,
        identity_keys=tuple(identity_keys),
    )


def _state_fingerprint(
    host: DamagePreventionCreationHost,
    subjects: Sequence[DamageSubject],
) -> str:
    return stable_json(
        {
            "existing": [
                shield.to_dict()
                for shield in host.state.damage_prevention_shields
            ],
            "subjects": [subject.to_dict() for subject in subjects],
        }
    )


def _shield_ids(
    host: DamagePreventionCreationHost,
    request: PreventionShieldCreationRequest,
    subjects: Sequence[DamageSubject],
) -> tuple[str, ...]:
    seed = stable_json(
        {
            "revision": int(host.state.revision),
            "event_sequence": int(host.state.event_sequence),
            "source": request.source_id,
            "controller": request.controller,
            "mode": request.mode.value,
            "duration": request.duration.value,
            "subjects": [subject.to_dict() for subject in subjects],
            "amounts": [allocation.amount for allocation in request.subjects],
            "chosen_source": request.chosen_source_ref,
            "source_predicate": request.source_predicate.to_dict(),
            "application_group_id": request.application_group_id,
            "aftermath": [
                (
                    {
                        "kind": "gain_life",
                        "player": value.player,
                        "per_prevented": value.per_prevented,
                        "fixed_amount": value.fixed_amount,
                    }
                    if isinstance(value, GainLifeAftermathRequest)
                    else {
                        "kind": "deal_damage",
                        "source": value.source_ref,
                        "recipient": value.recipient_ref,
                        "recipient_kind": value.recipient_kind,
                        "per_prevented": value.per_prevented,
                        "fixed_amount": value.fixed_amount,
                    }
                    if isinstance(value, DealDamageAftermathRequest)
                    else {
                        "kind": "place_counters",
                        "subject": value.subject_ref,
                        "counter_name": value.counter_name,
                        "placing_player": value.placing_player,
                        "per_prevented": value.per_prevented,
                        "fixed_amount": value.fixed_amount,
                    }
                )
                for value in request.aftermath
            ],
            "triggered_ability": (
                {
                    "source": request.triggered_ability.source_ref,
                    "label": request.triggered_ability.label,
                    "target_schema": thaw_value(
                        request.triggered_ability.target_schema
                    ),
                    "results": [
                        (
                            {
                                "kind": "draw_cards",
                                "player": value.player,
                                "per_prevented": value.per_prevented,
                                "fixed_amount": value.fixed_amount,
                                "private": value.private,
                            }
                            if isinstance(value, DrawCardsTriggerRequest)
                            else {
                                "kind": "deal_damage",
                                "source": value.source_ref,
                                "recipient_kind": value.recipient_kind,
                                "per_prevented": value.per_prevented,
                                "fixed_amount": value.fixed_amount,
                            }
                            if isinstance(value, DealDamageTriggerRequest)
                            else {
                                "kind": "place_counters",
                                "subject": value.subject_ref,
                                "counter_name": value.counter_name,
                                "placing_player": value.placing_player,
                                "per_prevented": value.per_prevented,
                                "fixed_amount": value.fixed_amount,
                            }
                        )
                        for value in request.triggered_ability.results
                    ],
                }
                if request.triggered_ability is not None
                else None
            ),
            "existing": [
                shield.shield_id
                for shield in host.state.damage_prevention_shields
            ],
        }
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return tuple(f"PS-{digest}-{index + 1}" for index in range(len(subjects)))


def _aftermath_for_subject(
    host: DamagePreventionCreationHost,
    request: PreventionShieldCreationRequest,
    subject: DamageSubject,
) -> tuple[
    GainLifePreventionAftermath
    | PlaceCountersPreventionAftermath
    | DealDamagePreventionAftermath,
    ...,
]:
    result: list[
        GainLifePreventionAftermath
        | PlaceCountersPreventionAftermath
        | DealDamagePreventionAftermath
    ] = []
    for value in request.aftermath:
        if isinstance(value, GainLifeAftermathRequest):
            result.append(
                GainLifePreventionAftermath(
                    player=value.player,
                    per_prevented=value.per_prevented,
                    fixed_amount=value.fixed_amount,
                )
            )
            continue
        if isinstance(value, DealDamageAftermathRequest):
            try:
                source = source_snapshot(
                    host,
                    value.source_ref,
                    controller=request.controller,
                )
            except DamageError as exc:
                raise DamagePreventionCreationError(str(exc)) from exc
            recipient = DamageAftermathRecipient(
                kind=value.recipient_kind,
                subject=(
                    _damage_subject(
                        host,
                        str(value.recipient_ref),
                        request.controller,
                    )
                    if value.recipient_ref is not None
                    else None
                ),
            )
            result.append(
                DealDamagePreventionAftermath(
                    source=source,
                    recipient=recipient,
                    per_prevented=value.per_prevented,
                    fixed_amount=value.fixed_amount,
                )
            )
            continue
        target = (
            subject
            if value.subject_ref is None
            else _damage_subject(host, value.subject_ref, request.controller)
        )
        result.append(
            PlaceCountersPreventionAftermath(
                subject=target,
                counter_name=value.counter_name,
                placing_player=value.placing_player,
                per_prevented=value.per_prevented,
                fixed_amount=value.fixed_amount,
            )
        )
    return tuple(result)


def _triggered_ability(
    host: DamagePreventionCreationHost,
    request: PreventionShieldCreationRequest,
) -> PreventionTriggeredAbility | None:
    value = request.triggered_ability
    if value is None:
        return None
    try:
        ability_source = source_snapshot(
            host,
            value.source_ref,
            controller=request.controller,
        )
        results = []
        for result in value.results:
            if isinstance(result, DrawCardsTriggerRequest):
                results.append(
                    DrawCardsPreventionTrigger(
                        player=result.player,
                        per_prevented=result.per_prevented,
                        fixed_amount=result.fixed_amount,
                        private=result.private,
                    )
                )
            elif isinstance(result, DealDamageTriggerRequest):
                results.append(
                    DealDamagePreventionTrigger(
                        source=source_snapshot(
                            host,
                            result.source_ref,
                            controller=request.controller,
                        ),
                        recipient_kind=result.recipient_kind,
                        per_prevented=result.per_prevented,
                        fixed_amount=result.fixed_amount,
                    )
                )
            else:
                results.append(
                    PlaceCountersPreventionTrigger(
                        subject_ref=result.subject_ref,
                        counter_name=result.counter_name,
                        placing_player=result.placing_player,
                        per_prevented=result.per_prevented,
                        fixed_amount=result.fixed_amount,
                    )
                )
        return PreventionTriggeredAbility(
            controller=request.controller,
            source=ability_source,
            label=value.label,
            results=tuple(results),
            target_schema=value.target_schema,
        )
    except (DamageError, PreventionTriggerError) as exc:
        raise DamagePreventionCreationError(str(exc)) from exc


def plan_prevention_shield_creation(
    host: DamagePreventionCreationHost,
    request: PreventionShieldCreationRequest,
) -> DamagePreventionCreationPlan:
    """Resolve subjects and source LKI without mutating authoritative state."""

    if request.controller not in host.state.active_seats():
        raise DamagePreventionCreationError(
            "The prevention effect controller is not active"
        )
    try:
        subjects = tuple(
            _damage_subject(host, allocation.subject_ref, request.controller)
            for allocation in request.subjects
        )
        chosen = pin_chosen_damage_source(
            host,
            source_ref=request.chosen_source_ref,
            controller=request.controller,
            predicate=request.source_predicate,
        )
        ids = _shield_ids(host, request, subjects)
        triggered_ability = _triggered_ability(host, request)
        shields = tuple(
            DamagePreventionShield(
                shield_id=shield_id,
                source_id=request.source_id,
                controller=request.controller,
                subject=subject,
                mode=request.mode,
                remaining=allocation.amount,
                duration=request.duration,
                created_turn_sequence=int(host.state.turn_sequence),
                damage_kind=request.damage_kind,
                recipient_kind=request.recipient_kind,
                scope=request.scope,
                chosen_source=chosen,
                label=request.label,
                application_group_id=request.application_group_id,
                aftermath=_aftermath_for_subject(host, request, subject),
                triggered_ability=triggered_ability,
            )
            for shield_id, subject, allocation in zip(
                ids, subjects, request.subjects, strict=True
            )
        )
    except (DamageError, DamageModifierError) as exc:
        raise DamagePreventionCreationError(str(exc)) from exc
    return DamagePreventionCreationPlan(
        state_fingerprint=_state_fingerprint(host, subjects),
        shields=shields,
    )


def validate_prevention_shield_creation(
    host: DamagePreventionCreationHost,
    plan: DamagePreventionCreationPlan,
) -> None:
    if not isinstance(plan, DamagePreventionCreationPlan) or not plan.shields:
        raise DamagePreventionCreationError(
            "Prevention commits require a nonempty typed plan"
        )
    subjects = tuple(shield.subject for shield in plan.shields)
    if plan.state_fingerprint != _state_fingerprint(host, subjects):
        raise DamagePreventionCreationError(
            "Prevention creation subject identity or state is stale"
        )
    current_ids = {
        shield.shield_id for shield in host.state.damage_prevention_shields
    }
    planned_ids = tuple(shield.shield_id for shield in plan.shields)
    if len(planned_ids) != len(set(planned_ids)) or current_ids.intersection(
        planned_ids
    ):
        raise DamagePreventionCreationError(
            "Prevention shield identity collision"
        )
    for subject in subjects:
        if subject.kind != "permanent":
            continue
        card = host.state.cards.get(str(subject.object_id or ""))
        if (
            card is None
            or card.zone != "battlefield"
            or card.logical_object_id != subject.logical_object_id
        ):
            raise DamagePreventionCreationError(
                "Prevention creation subject changed object identity"
            )


def commit_prevention_shield_creation(
    host: DamagePreventionCreationHost,
    plan: DamagePreventionCreationPlan,
) -> tuple[DamagePreventionShield, ...]:
    validate_prevention_shield_creation(host, plan)
    host.state.damage_prevention_shields.extend(plan.shields)
    return plan.shields


__all__ = [
    "DamagePreventionCreationError",
    "DamagePreventionCreationPlan",
    "DealDamageTriggerRequest",
    "DrawCardsTriggerRequest",
    "GainLifeAftermathRequest",
    "PlaceCountersTriggerRequest",
    "PlaceCountersAftermathRequest",
    "PreventionShieldCreationRequest",
    "PreventionSubjectAllocation",
    "PreventionTriggeredAbilityRequest",
    "commit_prevention_shield_creation",
    "plan_prevention_shield_creation",
    "pin_chosen_damage_source",
    "validate_prevention_shield_creation",
]
