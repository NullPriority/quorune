from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .counter_state import (
    CounterChange,
    CounterStateError,
    commit_counter_changes,
    plan_counter_changes,
)
from .damage_results import (
    commit_damage_result_plan,
    DamageResultError,
    DamageResultRecord,
    plan_damage_result_commit,
    prepare_damage_results,
    PreparedDamageResults,
)
from .damage_transaction import (
    DamageTransactionPort,
    DamageTransactionResult,
    PreparedDamageTransaction,
)
from .damage_values import (
    DamageError,
    DamageProposal,
    DamageRecipientKind,
    DamageRecipientSnapshot,
    DamageSourceSnapshot,
)
from .damage_source import represented_toxic_value
from .deathtouch import DeathtouchError, deathtouch_damage_result_applies
from .commander import CommanderIdentityError, commander_damage_key
from .combat_damage_events import (
    canonical_combat_assignment_values,
    combat_damage_event_identity,
    replacement_event_identity_values,
)
from .combat_damage_values import CombatDamageAssignmentError
from .damage_prevention import (
    collect_damage_modifier_effects,
    commit_damage_modifier_plan,
    damage_modifier_snapshot,
    DamageModifierCommitPlan,
    DamageModifierError,
    DamageModifierSnapshot,
    plan_damage_modifier_commit,
    project_damage_modifier_snapshot,
    validate_damage_modifier_plan,
)
from .damage_prevention_aftermath import (
    commit_prevention_aftermath,
    PreparedPreventionAftermath,
    prepare_prevention_aftermath,
    PreventionAftermathError,
    PreventionAftermathEvent,
    validate_prevention_aftermath,
)
from .replacement_effects import (
    ReplaceableEvent,
    ReplacementChoiceRequired,
    ReplacementClass,
    ReplacementEffect,
    ReplacementEffectError,
    ReplacementEventBatch,
    ReplacementSelection,
    advance_replacement_batch,
)
from .prevention_triggers import (
    PreventionTriggeredAbility,
    PreventionTriggerError,
    PreventionTriggerOccurrence,
    prevention_trigger_stack_item,
)
from .protection import (
    ProtectionSource,
    ProtectionVerdict,
    protection_verdict,
)
from .player_result_events import (
    dispatch_lifelink_gain_events,
    dispatch_prevention_life_gain_event,
)
from .trigger_processing import enqueue_trigger_batch


class DamageHost(Protocol):
    state: Any
    semantics: Any
    active_seats: list[str]
    seats: list[str]

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, kind: str, ref: str) -> str: ...

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def apnap_order(self, *, start: str | None = None) -> list[str]: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str] | None = None,
    ) -> Any: ...

    def _combat_damage_target_exists(self, target: str) -> bool: ...

    def _combat_keywords(self, card: Any) -> set[str]: ...

    def _queue_siege_defeated_trigger(self, battle: Any) -> None: ...

    def _monarch_trigger(self, **kwargs: Any) -> Any: ...

    def _dispatch_semantic_event(
        self,
        event: str,
        context: Mapping[str, Any],
        **kwargs: Any,
    ) -> None: ...

    def _semantic_pause_annotation(self) -> Mapping[str, Any] | None: ...

    def _record_turn_history(
        self,
        kind: str,
        *,
        actor: str | None = None,
        object_incarnation: str | None = None,
        target: str | None = None,
        target_kind: str | None = None,
        target_object_incarnation: str | None = None,
        amount: int | None = None,
    ) -> None: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        *,
        visibility: Sequence[str] | None = None,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> None: ...


def _normalized_keywords(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                " ".join(str(value).casefold().split())
                for value in values
                if str(value).strip()
            }
        )
    )


@dataclass(frozen=True, slots=True)
class DamageEvent:
    """One final source-recipient result from an authoritative damage batch.

    ``assigned_amount`` is the positive amount proposed by the producer before
    CR 614/615 processing. Replacement and prevention effects may increase,
    decrease, or prevent damage in an interleaved order, so dealt plus
    prevented damage is intentionally not required to equal the proposal.
    """

    source: str
    source_object_id: str
    source_logical_object_id: str
    source_oracle_id: str | None
    source_commander_designation_id: str | None
    source_controller: str
    source_owner: str
    source_types: tuple[str, ...]
    source_subtypes: tuple[str, ...]
    source_colors: tuple[str, ...]
    source_keywords: tuple[str, ...]
    source_is_commander: bool
    target: str
    target_kind: DamageRecipientKind
    target_object_id: str | None
    target_controller: str | None
    target_types: tuple[str, ...]
    target_subtypes: tuple[str, ...]
    assigned_amount: int
    dealt_amount: int
    prevented_amount: int
    combat: bool
    target_logical_object_id: str | None = None
    damage_step: int | None = None
    first_strike_step: bool = False
    unpreventable: bool = False
    applied_effects: tuple[str, ...] = ()
    source_toxic_value: int | None = 0
    source_zone: str = "unknown"
    source_supertypes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.target_kind not in {"player", "permanent"}:
            raise ValueError(
                "Damage target kinds must be player or permanent"
            )
        if self.assigned_amount <= 0:
            raise ValueError("A damage event requires a positive assignment")
        if self.dealt_amount < 0 or self.prevented_amount < 0:
            raise ValueError("Damage event results cannot be negative")
        if self.target_kind == "player" and self.target_object_id is not None:
            raise ValueError("Player damage cannot have a target object id")
        if self.target_kind == "permanent" and not self.target_object_id:
            raise ValueError("Permanent damage requires a target object id")
        if self.target_kind == "player" and self.target_logical_object_id is not None:
            raise ValueError("Player damage cannot have a target logical object id")
        if self.target_kind == "permanent" and not self.target_logical_object_id:
            raise ValueError(
                "Permanent damage requires a target logical object identity"
            )
        if len(self.applied_effects) != len(set(self.applied_effects)):
            raise ValueError(
                "A damage event cannot apply one replacement effect twice"
            )
        if self.source_toxic_value is not None and (
            type(self.source_toxic_value) is not int
            or self.source_toxic_value < 0
        ):
            raise ValueError(
                "A known total toxic value must be a nonnegative integer"
            )

    @property
    def was_dealt(self) -> bool:
        return self.dealt_amount > 0

    def semantic_context(self) -> dict[str, Any]:
        """Return the stable normalized context consumed by trigger programs."""

        return {
            # ``card`` is the established self-event identity field used by
            # ``damage.dealt.self`` programs.
            "card": self.source,
            "source": self.source,
            "source_object_id": self.source_object_id,
            "source_logical_object_id": self.source_logical_object_id,
            "source_zone": self.source_zone,
            "source_identity_key": (
                f"{self.source_logical_object_id}|{self.source_zone}"
            ),
            "source_oracle_id": self.source_oracle_id,
            "source_commander_designation_id": (
                self.source_commander_designation_id
            ),
            "source_controller": self.source_controller,
            "source_owner": self.source_owner,
            "source_types": list(self.source_types),
            "source_subtypes": list(self.source_subtypes),
            "source_supertypes": list(self.source_supertypes),
            "source_colors": list(self.source_colors),
            "source_keywords": list(self.source_keywords),
            "source_is_commander": self.source_is_commander,
            "source_toxic_value": self.source_toxic_value,
            "target": self.target,
            "target_kind": self.target_kind,
            "target_object_id": self.target_object_id,
            "target_logical_object_id": self.target_logical_object_id,
            "target_controller": self.target_controller,
            "target_types": list(self.target_types),
            "target_subtypes": list(self.target_subtypes),
            "player": self.target if self.target_kind == "player" else None,
            "amount": self.dealt_amount,
            "assigned_amount": self.assigned_amount,
            "prevented_amount": self.prevented_amount,
            "combat": self.combat,
            "damage_step": self.damage_step,
            "first_strike_step": self.first_strike_step,
            "unpreventable": self.unpreventable,
            "applied_effects": list(self.applied_effects),
        }


@dataclass(frozen=True, slots=True)
class PreparedDamageBatch:
    events: tuple[ReplaceableEvent, ...]
    effects: tuple[ReplacementEffect, ...]
    journal: tuple[ReplacementSelection, ...]
    result_events: tuple[ReplaceableEvent, ...] = ()
    result_effects: tuple[ReplacementEffect, ...] = ()
    result_journal: tuple[ReplacementSelection, ...] = ()
    modifier_plan: DamageModifierCommitPlan = DamageModifierCommitPlan()
    aftermath: PreparedPreventionAftermath = PreparedPreventionAftermath()
    consumed_selections: int = 0


@dataclass(frozen=True, slots=True)
class DamageLifeGain:
    player: str
    source: str
    amount: int


@dataclass(frozen=True, slots=True)
class PreventionAppliedEvent:
    effect_id: str
    source_id: str
    prevented_amount: int
    damage_event_ids: tuple[str, ...]
    prevented_source_controllers: tuple[str, ...] = ()
    affected_players: tuple[str, ...] = ()
    affected_permanents: tuple[str, ...] = ()
    triggered_ability: PreventionTriggeredAbility | None = None

    def semantic_context(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "source": self.source_id,
            "prevented_amount": self.prevented_amount,
            "damage_event_ids": list(self.damage_event_ids),
            "prevented_source_controllers": list(
                self.prevented_source_controllers
            ),
            "affected_players": list(self.affected_players),
            "affected_permanents": list(self.affected_permanents),
        }

    def trigger_occurrence(self) -> PreventionTriggerOccurrence | None:
        if self.triggered_ability is None:
            return None
        return PreventionTriggerOccurrence(
            ability=self.triggered_ability,
            effect_id=self.effect_id,
            prevented_amount=self.prevented_amount,
            damage_event_ids=self.damage_event_ids,
            prevented_source_controllers=self.prevented_source_controllers,
        )


@dataclass(frozen=True, slots=True)
class DamageBatchResult:
    events: tuple[DamageEvent, ...]
    changed_objects: tuple[str, ...]
    changed_players: tuple[str, ...]
    lifelink_gains: tuple[DamageLifeGain, ...]
    result_events: tuple[DamageResultRecord, ...] = ()
    prevention_events: tuple[PreventionAppliedEvent, ...] = ()
    aftermath_events: tuple[PreventionAftermathEvent, ...] = ()
    nested_damage_results: tuple["DamageBatchResult", ...] = ()

    @property
    def dealt_amount(self) -> int:
        return sum(event.dealt_amount for event in self.events)


@dataclass(frozen=True, slots=True)
class _CanonicalDamageTransactionPort(DamageTransactionPort):
    host: DamageHost

    def recipient(
        self,
        ref: str,
        *,
        actor: str,
    ) -> DamageRecipientSnapshot:
        return recipient_snapshot(self.host, ref, actor=actor)

    def prepare(
        self,
        proposals: Sequence[DamageProposal],
        *,
        selections: Sequence[str | None | Mapping[str, object]],
        sources: Sequence[object] | None,
        source_zones: Mapping[str, str] | None,
        modifier_snapshot: DamageModifierSnapshot,
        aftermath_depth: int,
        aftermath_effect_chain: tuple[str, ...],
    ) -> PreparedDamageTransaction:
        return prepare_damage_batch(
            self.host,
            proposals,
            selections=selections,
            sources=sources,
            source_zones=source_zones,
            modifier_snapshot=modifier_snapshot,
            aftermath_depth=aftermath_depth,
            aftermath_effect_chain=aftermath_effect_chain,
        )

    def commit(
        self,
        prepared: PreparedDamageTransaction,
    ) -> DamageTransactionResult:
        if not isinstance(prepared, PreparedDamageBatch):
            raise DamageError(
                "Nested damage lost its canonical prepared value"
            )
        return commit_prepared_damage_batch(self.host, prepared)


_DAMAGE_REASON_FIELD = "".join(("rea", "son"))


def source_snapshot(
    host: DamageHost,
    source_ref: str | None,
    *,
    controller: str,
) -> DamageSourceSnapshot:
    """Capture CR 120.1/120.2 source facts before event transformation."""

    source = next(
        (
            card
            for card in host.state.cards.values()
            if card.ref == source_ref
        ),
        None,
    )
    if source is None:
        stack_item = next(
            (
                item
                for item in host.state.stack
                if item.ref == source_ref and item.card_object_id
            ),
            None,
        )
        if stack_item is not None:
            source = host.state.cards.get(stack_item.card_object_id)
    if source is None:
        # Direct low-level effect calls and legacy checkpoints may not carry a
        # card handle. Keep their deterministic identity while withholding all
        # source characteristics; ordinary compiled damage always supplies the
        # exact source. This compatibility source does not claim CR 120.2
        # coverage for arbitrary off-battlefield objects.
        ref = str(source_ref or f"legacy-effect:{controller}")
        return DamageSourceSnapshot(
            ref=ref,
            object_id=f"unrepresented:{ref}",
            logical_object_id=f"unrepresented:{ref}",
            controller=controller,
            owner=controller,
            zone="unknown",
        )
    data = host._effective_card_data(source)
    card_types, subtypes, supertypes = host._type_parts(
        str(data.get("type_line") or "")
    )
    keywords = _normalized_keywords(data.get("keywords", ()))
    toxic_value = represented_toxic_value(
        data,
        temporary_keywords=getattr(source, "temporary_keywords", ()),
    )
    if toxic_value != 0 and "toxic" not in keywords:
        keywords = tuple(sorted({*keywords, "toxic"}))
    return DamageSourceSnapshot(
        ref=source.ref,
        object_id=source.object_id,
        logical_object_id=source.logical_object_id,
        controller=(
            source.controller
            if source.controller in host.state.players
            else controller
        ),
        owner=source.owner,
        zone=source.zone,
        oracle_id=source.oracle_id,
        commander_designation_id=source.commander_designation_id,
        types=tuple(sorted(card_types)),
        subtypes=tuple(sorted(subtypes)),
        supertypes=tuple(sorted(supertypes)),
        colors=tuple(
            sorted(str(value).upper() for value in data.get("colors", ()))
        ),
        keywords=keywords,
        mana_value=data.get("mana_value"),
        is_commander=bool(source.is_commander),
        toxic_value=toxic_value,
    )


def recipient_snapshot(
    host: DamageHost,
    target: str,
    *,
    actor: str,
) -> DamageRecipientSnapshot:
    if target in host.state.players:
        if target not in host.active_seats:
            raise DamageError("Damage cannot be dealt to a player who left")
        return DamageRecipientSnapshot(
            ref=target,
            kind="player",
            controller=target,
        )
    card = host._resolve_object(actor, target, zones={"battlefield"})
    data = host._effective_card_data(card)
    card_types, subtypes, _supertypes = host._type_parts(
        str(data.get("type_line") or "")
    )
    if not card_types.intersection({"battle", "creature", "planeswalker"}):
        raise DamageError(
            f"Damage cannot be dealt to {card.ref}; it is not a Battle, "
            "creature, or planeswalker"
        )
    return DamageRecipientSnapshot(
        ref=card.ref,
        kind="permanent",
        controller=card.controller,
        object_id=card.object_id,
        logical_object_id=card.logical_object_id,
        owner=card.owner,
        types=tuple(sorted(card_types)),
        subtypes=tuple(sorted(subtypes)),
    )


def damage_proposal(
    host: DamageHost,
    *,
    proposal_id: str,
    actor: str,
    source_ref: str | None,
    target: str,
    amount: int,
    combat: bool,
    reason: str,
    unpreventable: bool = False,
    damage_step: int | None = None,
    first_strike_step: bool = False,
    source_override: DamageSourceSnapshot | None = None,
) -> DamageProposal:
    if source_override is not None and not isinstance(
        source_override, DamageSourceSnapshot
    ):
        raise DamageError("Damage source overrides must be typed LKI snapshots")
    return DamageProposal(
        proposal_id=proposal_id,
        source=(
            source_override
            if source_override is not None
            else source_snapshot(host, source_ref, controller=actor)
        ),
        recipient=recipient_snapshot(host, target, actor=actor),
        amount=amount,
        combat=combat,
        reason=reason,
        unpreventable=unpreventable,
        damage_step=damage_step,
        first_strike_step=first_strike_step,
    )


def combat_damage_proposals(
    host: DamageHost,
    assignments: Sequence[Mapping[str, Any]],
    *,
    damage_step_id: str | None = None,
    replacement_event_ids: Sequence[str] = (),
) -> tuple[DamageProposal, ...]:
    """Materialize current combat assignments as immutable damage proposals."""

    try:
        canonical = canonical_combat_assignment_values(assignments)
        replacement_ids = replacement_event_identity_values(
            replacement_event_ids
        )
    except CombatDamageAssignmentError as exc:
        raise DamageError(str(exc)) from exc

    proposals: list[DamageProposal] = []
    event_index = 0
    for original_index, assignment in canonical:
        source_ref = assignment.source
        target = assignment.target
        amount = assignment.amount
        source = next(
            (
                card
                for card in host.state.cards.values()
                if card.ref == source_ref
            ),
            None,
        )
        if source is None:
            raise DamageError(f"Unknown damage source {source_ref}")
        if amount == 0:
            # CR 120.8: zero produces no event or replacement window.
            continue
        if source.zone != "battlefield":
            host._log(
                source.controller,
                "combat.damage.no_source",
                (
                    f"{source.ref} assigned no combat damage because "
                    "it was no longer on the battlefield."
                ),
                {"source": source.ref, "target": target, "amount": amount},
                importance=1,
            )
            continue
        if not host._combat_damage_target_exists(target):
            host._log(
                source.controller,
                "combat.damage.no_target",
                (
                    f"{source.ref} assigned no combat damage; {target} "
                    "was no longer a legal damage recipient."
                ),
                {"source": source.ref, "target": target, "amount": amount},
                importance=1,
            )
            continue
        if replacement_ids and event_index >= len(replacement_ids):
            raise DamageError("Combat replacement event identity count is stale")
        source_value = source_snapshot(
            host,
            source.ref,
            controller=source.controller,
        )
        recipient_value = recipient_snapshot(
            host,
            target,
            actor=source.controller,
        )
        recipient_identity = (
            recipient_value.logical_object_id
            if recipient_value.logical_object_id is not None
            else f"player:{recipient_value.ref}"
        )
        proposals.append(
            DamageProposal(
                proposal_id=(
                    replacement_ids[event_index]
                    if replacement_ids
                    else (
                        combat_damage_event_identity(
                            damage_step_id=damage_step_id,
                            source_logical_object_id=(
                                source_value.logical_object_id
                            ),
                            recipient_logical_object_id=recipient_identity,
                            amount=amount,
                        )
                        if damage_step_id is not None
                        else (
                            f"damage.combat:{host.state.revision}:"
                            f"{host.state.event_sequence + 1}:{original_index}"
                        )
                    )
                ),
                source=source_value,
                recipient=recipient_value,
                amount=amount,
                combat=True,
                reason="combat damage",
                damage_step=host.state.combat.damage_step_index + 1,
                first_strike_step=host.state.combat.first_strike_step,
            )
        )
        event_index += 1
    if replacement_ids and event_index != len(replacement_ids):
        raise DamageError("Combat replacement event identity count is stale")
    return tuple(proposals)


def _protection_prevention_effects(
    host: DamageHost,
    proposals: Sequence[DamageProposal],
) -> tuple[ReplacementEffect, ...]:
    effects: dict[str, ReplacementEffect] = {}
    for proposal in proposals:
        recipient = proposal.recipient
        source = proposal.source
        protected = False
        source_id = ""
        if recipient.kind == "player":
            protected = bool(
                host.state.players[recipient.ref].stats.get(
                    "protection_from_everything_until_next_turn"
                )
            )
            source_id = f"rules:protection:{recipient.ref}"
        else:
            assert recipient.object_id is not None
            card = host.state.cards.get(recipient.object_id)
            if card is not None:
                verdict = protection_verdict(
                    host._effective_card_data(card),
                    ProtectionSource(
                        colors=frozenset(source.colors),
                        card_types=frozenset(source.types),
                        subtypes=frozenset(source.subtypes),
                        supertypes=frozenset(source.supertypes),
                        mana_value=source.mana_value,
                    ),
                )
                if verdict is ProtectionVerdict.UNRESOLVED:
                    raise DamageError(
                        "Damage recipient has unresolved protection semantics"
                    )
                protected = verdict is ProtectionVerdict.BLOCKED
            source_id = f"rules:protection:{recipient.object_id}"
        if not protected:
            continue
        effect_id = (
            f"prevention.protection:{recipient.ref}:{source.ref}"
        )
        effects[effect_id] = ReplacementEffect(
            effect_id=effect_id,
            source_id=source_id,
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "amount": {"not_in": [0]},
                "source": {"eq": source.ref},
                "target": {"eq": recipient.ref},
            },
            operations=({"op": "prevent"},),
            label=f"Protection prevents damage to {recipient.ref}",
        )
    return tuple(effects[key] for key in sorted(effects))


def prepare_damage_batch(
    host: DamageHost,
    proposals: Sequence[DamageProposal],
    *,
    selections: Sequence[str | None | Mapping[str, Any]] = (),
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
    modifier_snapshot: DamageModifierSnapshot | None = None,
    aftermath_depth: int = 0,
    aftermath_effect_chain: tuple[str, ...] = (),
) -> PreparedDamageBatch:
    """Resolve CR 120.4b damage and CR 120.4c results before mutation."""

    current_modifiers = modifier_snapshot or damage_modifier_snapshot(host)
    nonzero = tuple(proposal for proposal in proposals if proposal.amount > 0)
    if not nonzero:
        if selections:
            raise DamageError(
                "Replacement selections were supplied without damage"
            )
        # An empty damage batch is still prepared and committed through the
        # same atomic boundary. Pin the durable modifier state so commit can
        # distinguish a legitimate zero-event batch from a stale/default
        # PreparedDamageBatch assembled outside this function.
        try:
            modifier_plan = plan_damage_modifier_commit(
                host, (), snapshot=current_modifiers
            )
        except DamageModifierError as exc:
            raise DamageError(str(exc)) from exc
        return PreparedDamageBatch(
            events=(),
            effects=(),
            journal=(),
            modifier_plan=modifier_plan,
        )

    # Imported lazily to keep the immutable event model independent from the
    # CardProgram runtime registry that lowers ambient battlefield abilities.
    from .semantic_runtime.damage_replacements import (
        collect_damage_replacement_effects,
    )
    from .semantic_runtime.damage_results import (
        collect_damage_result_replacement_effects,
    )
    from .semantic_runtime.counter_replacements import (
        collect_counter_placement_replacement_effects,
    )

    effects = (
        *collect_damage_replacement_effects(
            host,
            sources=sources,
            source_zones=source_zones,
        ),
        *collect_damage_modifier_effects(host, snapshot=current_modifiers),
        *_protection_prevention_effects(host, nonzero),
    )
    events = tuple(proposal.event() for proposal in nonzero)
    if effects:
        damage_progress = advance_replacement_batch(
            ReplacementEventBatch(
                batch_id=(
                    f"replacement:damage:{host.state.revision}:"
                    f"{host.state.event_sequence + 1}"
                ),
                events=events,
                apnap_order=tuple(host.apnap_order()),
            ),
            effects,
            selections=selections,
            require_all_selections=False,
        )
        if damage_progress.pending is not None:
            raise ReplacementChoiceRequired(
                batch=damage_progress.batch,
                effects=effects,
                pending=damage_progress.pending,
            )
        events = damage_progress.batch.events
        damage_journal = damage_progress.batch.journal
        consumed = damage_progress.consumed_selections
    else:
        damage_journal = ()
        consumed = 0

    result_effects = (
        *collect_damage_result_replacement_effects(
            host,
            sources=sources,
            source_zones=source_zones,
        ),
        *collect_counter_placement_replacement_effects(
            host,
            sources=sources,
            source_zones=source_zones,
        ),
    )
    try:
        result_progress = prepare_damage_results(
            host,
            events,
            effects=result_effects,
            selections=tuple(selections[consumed:]),
            require_all_selections=False,
        )
    except (DamageResultError, ReplacementEffectError) as exc:
        raise DamageError(str(exc)) from exc
    if result_progress.pending is not None:
        raise ReplacementChoiceRequired(
            batch=ReplacementEventBatch(
                batch_id=(
                    f"replacement:damage.results:{host.state.revision}:"
                    f"{host.state.event_sequence + 1}"
                ),
                events=result_progress.events,
                apnap_order=tuple(host.apnap_order()),
                journal=result_progress.journal,
            ),
            effects=result_effects,
            pending=result_progress.pending,
        )
    try:
        modifier_plan = plan_damage_modifier_commit(
            host, events, snapshot=current_modifiers
        )
    except DamageModifierError as exc:
        raise DamageError(str(exc)) from exc
    aftermath_selection_offset = (
        consumed + result_progress.consumed_selections
    )
    try:
        aftermath = prepare_prevention_aftermath(
            host,
            events,
            damage_port=_CanonicalDamageTransactionPort(host),
            selections=tuple(selections[aftermath_selection_offset:]),
            sources=sources,
            source_zones=source_zones,
            modifier_snapshot=project_damage_modifier_snapshot(
                current_modifiers, modifier_plan
            ),
            depth=aftermath_depth,
            effect_chain=aftermath_effect_chain,
        )
    except PreventionAftermathError as exc:
        raise DamageError(str(exc)) from exc
    return PreparedDamageBatch(
        events=events,
        effects=tuple(effects),
        journal=tuple(damage_journal),
        result_events=result_progress.events,
        result_effects=tuple(result_effects),
        result_journal=result_progress.journal,
        modifier_plan=modifier_plan,
        aftermath=aftermath,
        consumed_selections=(
            aftermath_selection_offset + aftermath.consumed_selections
        ),
    )


def _permanent_result_plan(
    host: DamageHost,
    event: ReplaceableEvent,
    amount: int,
) -> tuple[Any, set[str]]:
    object_id = str(event.payload.get("target_object_id") or "")
    card = host.state.cards.get(object_id)
    if card is None or card.zone != "battlefield" or card.phased_out:
        raise DamageError("Damage recipient is no longer on the battlefield")
    if card.logical_object_id != str(
        event.payload.get("target_logical_object_id") or ""
    ):
        raise DamageError("Damage recipient changed object identity")
    data = host._effective_card_data(card)
    card_types, _subtypes, _supertypes = host._type_parts(
        str(data.get("type_line") or "")
    )
    damageable = card_types.intersection(
        {"battle", "creature", "planeswalker"}
    )
    if not damageable:
        raise DamageError(
            f"Damage cannot be dealt to {card.ref}; it is not damageable"
        )
    if amount < 0:
        raise DamageError("Resolved damage cannot be negative")
    return card, damageable


def apply_damage_results_to_permanent(
    host: DamageHost,
    card: Any,
    amount: int,
    *,
    source_keywords: Sequence[str] = (),
) -> dict[str, Any]:
    """Commit the represented CR 120.3 permanent results at one owner."""

    if type(amount) is not int:
        raise DamageError("Damage must be an integer")
    damage = amount
    if damage < 0:
        raise DamageError("Damage cannot be negative")
    data = host._effective_card_data(card)
    card_types, _subtypes, _supertypes = host._type_parts(
        str(data.get("type_line") or "")
    )
    damageable_types = card_types.intersection(
        {"battle", "creature", "planeswalker"}
    )
    if not damageable_types:
        raise DamageError(
            f"Damage cannot be dealt to {card.ref}; it is not a Battle, "
            "creature, or planeswalker"
        )
    keywords = set(_normalized_keywords(source_keywords))
    result: dict[str, Any] = {
        "amount": damage,
        "types": sorted(damageable_types),
    }
    if damage == 0:
        return result
    counter_changes: list[CounterChange] = []
    mark_damage = 0
    mark_deathtouch = False
    if "creature" in card_types:
        try:
            mark_deathtouch = deathtouch_damage_result_applies(
                amount=damage,
                source_keywords=source_keywords,
                target_types=card_types,
            )
        except DeathtouchError as exc:
            raise DamageError(str(exc)) from exc
        if keywords.intersection({"infect", "wither"}):
            counter_changes.append(
                CounterChange(
                    subject_kind="permanent",
                    subject_id=card.object_id,
                    counter_name="-1/-1",
                    amount=damage,
                    expected_zone=card.zone,
                    expected_logical_object_id=card.logical_object_id,
                )
            )
            result["minus_one_counters"] = damage
        else:
            mark_damage = damage
            result["marked_damage"] = damage
    for card_type, counter_name, result_name in (
        ("planeswalker", "loyalty", "loyalty_removed"),
        ("battle", "defense", "defense_removed"),
    ):
        if card_type not in card_types:
            continue
        counter_changes.append(
            CounterChange(
                subject_kind="permanent",
                subject_id=card.object_id,
                counter_name=counter_name,
                amount=-damage,
                expected_zone=card.zone,
                expected_logical_object_id=card.logical_object_id,
            )
        )
    try:
        transitions = commit_counter_changes(
            host, plan_counter_changes(host, tuple(counter_changes))
        )
    except CounterStateError as exc:
        raise DamageError(str(exc)) from exc
    for transition in transitions:
        if transition.counter_name == "loyalty":
            result["loyalty_removed"] = -transition.applied_delta
        elif transition.counter_name == "defense":
            result["defense_removed"] = -transition.applied_delta
            if transition.before > 0 and transition.after == 0:
                host._queue_siege_defeated_trigger(card)
    if "creature" in card_types:
        card.deathtouch_damage = (
            card.deathtouch_damage or mark_deathtouch
        )
        card.marked_damage += mark_damage
    return result


def _event_result(event: ReplaceableEvent) -> tuple[int, int, int]:
    proposed = int(event.payload.get("proposed_amount", -1))
    amount = int(event.payload.get("amount", -1))
    prevented = int(event.payload.get("prevented", 0))
    if proposed < 1 or amount < 0 or prevented < 0:
        raise DamageError("Resolved damage event produced invalid amounts")
    return proposed, amount, prevented


def _final_event(
    event: ReplaceableEvent,
    *,
    proposed: int,
    dealt: int,
    prevented: int,
) -> DamageEvent:
    payload = event.payload
    target_kind = str(payload.get("target_kind") or "")
    if target_kind not in {"player", "permanent"}:
        raise DamageError("Resolved damage event lost its recipient kind")
    return DamageEvent(
        source=str(payload.get("source") or ""),
        source_object_id=str(payload.get("source_object_id") or ""),
        source_logical_object_id=str(
            payload.get("source_logical_object_id") or ""
        ),
        source_zone=str(payload.get("source_zone") or "unknown"),
        source_oracle_id=(
            str(payload["source_oracle_id"])
            if payload.get("source_oracle_id") is not None
            else None
        ),
        source_commander_designation_id=(
            str(payload["source_commander_designation_id"])
            if payload.get("source_commander_designation_id") is not None
            else None
        ),
        source_controller=str(payload.get("source_controller") or ""),
        source_owner=str(payload.get("source_owner") or ""),
        source_types=tuple(str(value) for value in payload.get("source_types", ())),
        source_subtypes=tuple(
            str(value) for value in payload.get("source_subtypes", ())
        ),
        source_supertypes=tuple(
            str(value) for value in payload.get("source_supertypes", ())
        ),
        source_colors=tuple(
            str(value) for value in payload.get("source_colors", ())
        ),
        source_keywords=tuple(
            str(value) for value in payload.get("source_keywords", ())
        ),
        source_is_commander=bool(payload.get("source_is_commander")),
        target=str(payload.get("target") or ""),
        target_kind=target_kind,  # type: ignore[arg-type]
        target_object_id=(
            str(payload["target_object_id"])
            if payload.get("target_object_id") is not None
            else None
        ),
        target_logical_object_id=(
            str(payload["target_logical_object_id"])
            if payload.get("target_logical_object_id") is not None
            else None
        ),
        target_controller=(
            str(payload["target_controller"])
            if payload.get("target_controller") is not None
            else None
        ),
        target_types=tuple(
            str(value) for value in payload.get("target_types", ())
        ),
        target_subtypes=tuple(
            str(value) for value in payload.get("target_subtypes", ())
        ),
        assigned_amount=proposed,
        dealt_amount=dealt,
        prevented_amount=prevented,
        combat=bool(payload.get("combat")),
        damage_step=(
            int(payload["damage_step"])
            if payload.get("damage_step") is not None
            else None
        ),
        first_strike_step=bool(payload.get("first_strike_step")),
        unpreventable=bool(payload.get("unpreventable")),
        applied_effects=event.applied_effects,
        source_toxic_value=(
            int(payload["source_toxic_value"])
            if payload.get("source_toxic_value") is not None
            else None
        ),
    )


def _log_replacement_journal(
    host: DamageHost,
    prepared: PreparedDamageBatch,
) -> None:
    effects = {effect.effect_id: effect for effect in prepared.effects}
    events = {event.event_id: event for event in prepared.events}
    for selection in prepared.journal:
        selected_id = str(selection.effect_id or "")
        if selected_id.startswith("decline:"):
            continue
        effect = effects.get(selected_id)
        event = events.get(selection.event_id)
        if effect is None or event is None:
            raise DamageError(
                "Damage replacement journal does not match its snapshot"
            )
        host._log(
            None,
            "replacement.apply",
            f"{effect.source_id} modified a damage event.",
            {
                "source": effect.source_id,
                "effect_id": effect.effect_id,
                "damage_source": event.payload.get("source"),
                "target": event.payload.get("target"),
                "proposed": event.payload.get("proposed_amount"),
                "dealt": event.payload.get("amount"),
                "prevented": event.payload.get("prevented"),
            },
            importance=2,
        )


def _validate_damage_replacement_journal(
    prepared: PreparedDamageBatch,
) -> None:
    effects = {effect.effect_id for effect in prepared.effects}
    events = {event.event_id for event in prepared.events}
    for selection in prepared.journal:
        selected_id = str(selection.effect_id or "")
        if selected_id.startswith("decline:"):
            continue
        if selected_id not in effects or selection.event_id not in events:
            raise DamageError(
                "Damage replacement journal does not match its snapshot"
            )


def _validate_result_replacement_journal(
    prepared: PreparedDamageBatch,
) -> None:
    effects = {effect.effect_id: effect for effect in prepared.result_effects}
    events = {event.event_id: event for event in prepared.result_events}
    for selection in prepared.result_journal:
        selected_id = str(selection.effect_id or "")
        if selected_id.startswith("decline:"):
            continue
        if selected_id not in effects or selection.event_id not in events:
            raise DamageError(
                "Damage-result replacement journal does not match its snapshot"
            )


def _log_result_replacement_journal(
    host: DamageHost,
    prepared: PreparedDamageBatch,
) -> None:
    effects = {effect.effect_id: effect for effect in prepared.result_effects}
    events = {event.event_id: event for event in prepared.result_events}
    for selection in prepared.result_journal:
        selected_id = str(selection.effect_id or "")
        if selected_id.startswith("decline:"):
            continue
        effect = effects[selected_id]
        event = events[selection.event_id]
        host._log(
            None,
            "replacement.apply",
            f"{effect.source_id} modified a damage-result event.",
            {
                "source": effect.source_id,
                "effect_id": effect.effect_id,
                "result_subject": event.payload.get("subject"),
                "result_subject_kind": event.payload.get("subject_kind"),
                "event_path": list(selection.path),
            },
            importance=2,
        )


def _prevention_applied_events(
    host: DamageHost,
    prepared: PreparedDamageBatch,
) -> tuple[PreventionAppliedEvent, ...]:
    sources = {effect.effect_id: effect.source_id for effect in prepared.effects}
    shield_triggers = {
        shield.effect_id: shield.triggered_ability
        for shield in host.state.damage_prevention_shields
        if shield.triggered_ability is not None
    }
    amounts: dict[str, int] = {}
    event_ids: dict[str, list[str]] = {}
    source_controllers: dict[str, set[str]] = {}
    affected_players: dict[str, set[str]] = {}
    affected_permanents: dict[str, set[str]] = {}
    for event in prepared.events:
        by_effect = event.payload.get("prevention_applied") or {}
        if not isinstance(by_effect, Mapping):
            raise DamageError(
                "Resolved damage prevention journal is malformed"
            )
        for effect_id, raw_amount in by_effect.items():
            key = str(effect_id)
            if key not in sources or type(raw_amount) is not int or raw_amount < 0:
                raise DamageError(
                    "Resolved damage prevention journal is stale"
                )
            if raw_amount == 0:
                continue
            amounts[key] = amounts.get(key, 0) + raw_amount
            event_ids.setdefault(key, []).append(event.event_id)
            controller = str(event.payload.get("source_controller") or "")
            if not controller:
                raise DamageError(
                    "Resolved damage prevention lost source-controller LKI"
                )
            source_controllers.setdefault(key, set()).add(controller)
            target = str(event.payload.get("target") or "")
            target_kind = str(event.payload.get("target_kind") or "")
            if not target or target_kind not in {"player", "permanent"}:
                raise DamageError(
                    "Resolved damage prevention lost affected-subject identity"
                )
            destination = (
                affected_players
                if target_kind == "player"
                else affected_permanents
            )
            destination.setdefault(key, set()).add(target)
    result = tuple(
        PreventionAppliedEvent(
            effect_id=effect_id,
            source_id=sources[effect_id],
            prevented_amount=amounts[effect_id],
            damage_event_ids=tuple(sorted(event_ids[effect_id])),
            prevented_source_controllers=tuple(
                sorted(source_controllers[effect_id])
            ),
            affected_players=tuple(
                sorted(affected_players.get(effect_id, set()))
            ),
            affected_permanents=tuple(
                sorted(affected_permanents.get(effect_id, set()))
            ),
            triggered_ability=shield_triggers.get(effect_id),
        )
        for effect_id in sorted(amounts)
    )
    try:
        for event in result:
            occurrence = event.trigger_occurrence()
            if occurrence is not None:
                occurrence.runtime_effects()
    except PreventionTriggerError as exc:
        raise DamageError(str(exc)) from exc
    return result


def commit_prepared_damage_batch(
    host: DamageHost,
    prepared: PreparedDamageBatch,
    *,
    log_replacements: bool = True,
) -> DamageBatchResult:
    """Atomically validate and commit a choice-complete damage batch."""

    final_events: list[DamageEvent] = []
    for event in prepared.events:
        proposed, amount, prevented = _event_result(event)
        target_kind = str(event.payload.get("target_kind") or "")
        target = str(event.payload.get("target") or "")
        if target_kind == "player":
            if target not in host.active_seats:
                raise DamageError("Damage recipient is no longer in the game")
        elif target_kind == "permanent":
            _permanent_result_plan(host, event, amount)
        else:
            raise DamageError("Resolved damage event lost its recipient")
        final_events.append(
            _final_event(
                event,
                proposed=proposed,
                dealt=amount,
                prevented=prevented,
            )
        )

    has_results = any(
        int(event.payload.get("amount", 0)) > 0 or event.children
        for event in prepared.events
    )
    if has_results and not prepared.result_events:
        raise DamageError("Prepared damage is missing its result event batch")
    try:
        result_plan = plan_damage_result_commit(
            host,
            PreparedDamageResults(
                events=prepared.result_events,
                effects=prepared.result_effects,
                journal=prepared.result_journal,
            ),
        )
    except (DamageResultError, ReplacementEffectError) as exc:
        raise DamageError(str(exc)) from exc

    _validate_damage_replacement_journal(prepared)
    _validate_result_replacement_journal(prepared)
    try:
        validate_damage_modifier_plan(host, prepared.modifier_plan)
    except DamageModifierError as exc:
        raise DamageError(str(exc)) from exc
    try:
        validate_prevention_aftermath(host, prepared.aftermath)
    except PreventionAftermathError as exc:
        raise DamageError(str(exc)) from exc
    prevention_events = _prevention_applied_events(host, prepared)

    commander_updates: list[tuple[str, str, int]] = []
    history_events: list[DamageEvent] = []
    for final in final_events:
        if final.dealt_amount:
            history_events.append(final)
        if not (
            final.dealt_amount
            and final.target_kind == "player"
            and final.combat
            and final.source_is_commander
        ):
            continue
        try:
            commander_key = commander_damage_key(
                source_is_commander=final.source_is_commander,
                designation_id=final.source_commander_designation_id,
                oracle_id=final.source_oracle_id,
                identity_version=(
                    host.state.commander_damage_identity_version
                ),
            )
        except CommanderIdentityError as exc:
            raise DamageError(str(exc)) from exc
        if commander_key is None:
            raise DamageError("Commander damage lost its source identity")
        commander_updates.append(
            (final.target, commander_key, final.dealt_amount)
        )

    committed = commit_damage_result_plan(host, result_plan)
    try:
        commit_damage_modifier_plan(host, prepared.modifier_plan)
    except DamageModifierError as exc:
        raise DamageError(str(exc)) from exc
    try:
        aftermath = commit_prevention_aftermath(
            host,
            prepared.aftermath,
            damage_port=_CanonicalDamageTransactionPort(host),
        )
    except PreventionAftermathError as exc:
        raise DamageError(str(exc)) from exc
    changed_players = list(committed.changed_players)
    changed_objects = list(committed.changed_objects)
    changed_players.extend(aftermath.changed_players)
    changed_objects.extend(aftermath.changed_objects)
    for target, commander_key, amount in commander_updates:
        received = host.state.players[target].commander_damage_received
        received[commander_key] = received.get(commander_key, 0) + amount
        changed_players.append(target)
    for final in history_events:
        host._record_turn_history(
            (
                "player_damaged"
                if final.target_kind == "player"
                else "permanent_damaged"
            ),
            actor=final.source_controller,
            object_incarnation=final.source_logical_object_id,
            target=final.target,
            target_kind=final.target_kind,
            target_object_incarnation=final.target_logical_object_id,
            amount=final.dealt_amount,
        )

    gains = [
        DamageLifeGain(
            player=str(record.player),
            source=str(record.source),
            amount=record.amount,
        )
        for record in committed.records
        if record.kind == "life.change"
        and record.direction == "gain"
        and record.cause == "lifelink"
        and record.amount > 0
        and record.player is not None
        and record.source is not None
    ]

    if log_replacements:
        _log_replacement_journal(host, prepared)
        _log_result_replacement_journal(host, prepared)
    return DamageBatchResult(
        events=tuple(final_events),
        changed_objects=tuple(dict.fromkeys(changed_objects)),
        changed_players=tuple(dict.fromkeys(changed_players)),
        lifelink_gains=tuple(gains),
        result_events=committed.records,
        prevention_events=prevention_events,
        aftermath_events=aftermath.events,
        nested_damage_results=aftermath.nested_damage_results,
    )


def _collect_damage_result_triggers(
    host: DamageHost,
    result: DamageBatchResult,
    *,
    trigger_sources: Sequence[Any],
    trigger_source_zones: Mapping[str, str],
    trigger_batch: list[Any],
) -> None:
    dispatch_lifelink_gain_events(
        host,
        result.lifelink_gains,
        sources=trigger_sources,
        source_zones=trigger_source_zones,
        trigger_batch=trigger_batch,
    )

    for prevention in result.prevention_events:
        occurrence = prevention.trigger_occurrence()
        if occurrence is not None:
            ref = host._next_ref("S")
            trigger_batch.append(
                prevention_trigger_stack_item(
                    occurrence,
                    ref=ref,
                    stack_id=host._stable_runtime_id("stack", ref),
                    visibility=host.seats,
                )
            )
        host._dispatch_semantic_event(
            "damage.prevented",
            prevention.semantic_context(),
            sources=trigger_sources,
            source_zones=trigger_source_zones,
            trigger_batch=trigger_batch,
        )
        if host._semantic_pause_annotation() is not None:
            break
    if host._semantic_pause_annotation() is None:
        for event in result.events:
            if not event.was_dealt:
                continue
            if (
                host.state.monarch is not None
                and event.target_kind == "player"
                and event.target == host.state.monarch
                and event.combat
                and "creature" in event.source_types
                and event.source_controller in host.active_seats
            ):
                old_monarch = str(host.state.monarch)
                new_monarch = event.source_controller
                trigger_batch.append(
                    host._monarch_trigger(
                        controller=old_monarch,
                        label=(
                            "The monarch — "
                            f"{new_monarch} becomes the monarch"
                        ),
                        effects=(
                            {
                                "op": "become_monarch",
                                "player": new_monarch,
                                _DAMAGE_REASON_FIELD: (
                                    "a creature dealt combat damage to "
                                    "the monarch"
                                ),
                            },
                        ),
                        context={
                            "event": "damage.dealt",
                            "source": event.source,
                            "damaged_player": event.target,
                            "new_monarch": new_monarch,
                            "monarch_at_trigger": old_monarch,
                            "inherent_rule": "CR 725.2b",
                        },
                    )
                )
            host._dispatch_semantic_event(
                "damage.dealt",
                event.semantic_context(),
                sources=trigger_sources,
                source_zones=trigger_source_zones,
                trigger_batch=trigger_batch,
            )
            if host._semantic_pause_annotation() is not None:
                break
    nested_index = 0
    if host._semantic_pause_annotation() is None:
        for aftermath in result.aftermath_events:
            host._log(
                None,
                "damage.prevention.aftermath",
                f"{aftermath.source_id} applied a prevention aftermath.",
                aftermath.semantic_context(),
                importance=2,
                changed_players=(
                    [aftermath.subject]
                    if aftermath.kind in {"gain_life", "deal_damage"}
                    and aftermath.subject in host.active_seats
                    else []
                ),
            )
            dispatch_prevention_life_gain_event(
                host,
                aftermath,
                sources=trigger_sources,
                source_zones=trigger_source_zones,
                trigger_batch=trigger_batch,
            )
            host._dispatch_semantic_event(
                "damage.prevention.aftermath",
                aftermath.semantic_context(),
                sources=trigger_sources,
                source_zones=trigger_source_zones,
                trigger_batch=trigger_batch,
            )
            if aftermath.kind != "deal_damage":
                continue
            if nested_index >= len(result.nested_damage_results):
                raise DamageError(
                    "Prevention aftermath lost its nested damage result"
                )
            _collect_damage_result_triggers(
                host,
                result.nested_damage_results[nested_index],
                trigger_sources=trigger_sources,
                trigger_source_zones=trigger_source_zones,
                trigger_batch=trigger_batch,
            )
            nested_index += 1
    if nested_index != len(result.nested_damage_results):
        raise DamageError("Unexpected nested prevention damage result")


def resolve_damage_batch(
    host: DamageHost,
    proposals: Sequence[DamageProposal],
    *,
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
) -> DamageBatchResult:
    """Resolve one typed damage batch through results and trigger discovery."""

    trigger_sources = host._semantic_event_sources()
    trigger_source_zones = {
        source.object_id: source.zone for source in trigger_sources
    }
    prepared = prepare_damage_batch(
        host,
        proposals,
        selections=replacement_selections,
        sources=trigger_sources,
        source_zones=trigger_source_zones,
    )
    result = commit_prepared_damage_batch(host, prepared)

    trigger_batch: list[Any] = []
    _collect_damage_result_triggers(
        host,
        result,
        trigger_sources=trigger_sources,
        trigger_source_zones=trigger_source_zones,
        trigger_batch=trigger_batch,
    )
    enqueue_trigger_batch(host, trigger_batch)
    return result
