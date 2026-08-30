from __future__ import annotations

import copy
import hashlib
import random
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .abilities import (
    ActivatedAbility,
)
from .aura import (
    complete_aura_entry_choice,
    EnchantSpec,
    simple_aura_attachment_is_legal,
)
from .ability_fragment_host import AbilityFragmentHostMixin
from .ability_fragments import (
    counter_maximum_values,
    declaration_cost_specs,
    declaration_requirement_specs,
)
from .attachments import (
    detach_object,
)
from .carddb import CardDatabase, CardRecord
from .casting_cost_host import CastingCostHostMixin
from .compiled_flashback import (
    compiled_fixed_mana_flashback_spec,
    compiled_ordinary_zone_cast_permission,
)
from .card_programs.validation import (
    canonical_program_fingerprint,
    program_source_is_current,
)
from .rules.activation_costs import activation_choice_candidates
from .characteristic_evaluation import (
    type_parts,
)
from .characteristic_evaluation_host import CharacteristicEvaluationHostMixin
from .combat import (
    LIFELINK,
    assigns_in_damage_step,
    first_strike_step_required,
    normalized_keywords,
    ordinary_second_step_combatants,
)
from . import defender
from . import menace
from .combat_damage_assignment import CombatDamageAssignmentError
from .combat_damage_engine_adapter import EngineCombatDamageQuery
from .combat_damage_projection import project_combat_damage_assignment
from .combat_damage_sequence import (
    CombatDamageAnnouncement,
    CombatDamageAssignmentSequence,
    CombatDamageSequenceError,
)
from . import block_transition_engine_adapter as block_triggers
from . import attack_transition_engine_adapter as attack_transitions
from .combat_relationship_state import remove_combat_relationships
from . import control_history
from .counter_placement import (
    CounterPlacementError,
    place_counters_on_controlled_subtype,
    place_counters_on_refs,
)
from .counter_state import (
    CounterChange,
    CounterStateError,
    commit_counter_changes,
    plan_counter_changes,
)
from .crew import (
    CrewAbilityError,
    CrewCandidate,
    crew_candidates as current_crew_candidates,
    pay_crew_cost,
)
from .combat_constraints import (
    DeclarationConstraintError,
    DeclarationProblem,
    DeclarationRequirement,
    DeclarationRestriction,
    DeclarationSearchLimitError,
)
from .commander import initial_commander_state
from .commander_zones import (
    CommanderZoneError,
    pending_commander_zone_state_choices,
)
from .declaration_costs import (
    DeclarationCost,
)
from .declaration_requirement_runtime import (
    typed_attacker_block_requirements,
    typed_blocker_requirements,
)
from .declaration_restrictions import (
    DeclarationBattlefieldCondition,
    DeclarationCombatCondition,
    DeclarationCondition,
    DeclarationConditionPlayer,
    DeclarationObjectPredicate,
    DeclarationPlayerStateCondition,
    DeclarationRestrictionTemplate,
    DeclarationSharedSubtypeCondition,
    DeclarationTurnHistoryCondition,
)
from .rules.temporary_declaration_restrictions import (
    current_declaration_restrictions,
)
from .damage import (
    combat_damage_proposals,
    DamageError,
    resolve_damage_batch,
)
from .damage_results import (
    consume_deathtouch_damage_checks,
)
from .drawing import (
    begin_draw_batch,
    begin_draw_sequence,
    commit_unreplaced_draws,
    complete_draw_decision,
    DrawnCardAction,
    DrawError,
    QueuedDraw,
    resume_after_draw,
)
from .trigger_processing import (
    begin_pending_trigger_batch,
    begin_trigger_target_selection,
    collect_trigger_items,
    collect_ward_occurrences,
    complete_trigger_order_decision,
    enqueue_trigger_batch,
    TriggerProcessingHostMixin,
)
from .trigger_discovery import (
    dispatch_semantic_event,
    semantic_event_condition_matches,
    semantic_event_matches,
    semantic_event_value,
)
from .zone_trigger_events import (
    ZoneChangeOccurrence,
    ZoneTransitionKind,
)
from .zone_trigger_processing import (
    DepartureTriggerSnapshot,
)
from .zone_transition_model import ZoneDepartureSnapshot
from .zone_transitions import ZoneTransitionOwner
from .turn_priority_owner import TurnPriorityDecisionOwner
from .turn_step_owner import TURN_STEPS, TurnStepOwner
from .selection.targeting import TargetSelectionOwnerMixin
from .selection.searching import HiddenSearchOwnerMixin
from .selection.apnap import ApnapChoiceOwnerMixin
from .selection.storm import STORM_SEMANTIC_KEY, StormTargetChoiceOwnerMixin
from .selection.exile_cast import OneShotExileCastChoiceOwnerMixin
from .selection.public_choice import PublicChoiceOwnerMixin
from . import turn_counter_coordination
from .impulse_access import temporary_play_permission_is_current
from . import untap_step_coordination
from .saga_progression import saga_final_chapter_snapshot
from . import haste
from .keyword_abilities import normalized_characteristic_keywords
from .combat_evasion_engine_adapter import engine_combat_evasion_verdict
from .errors import GameRuleError, StateInvariantError
from .entry_counter_coordination import (
    prepare_resolving_entry_replacement,
)
from .deck import DeckDefinition
from .mana import (
    ManaMode,
    ManaPlanError,
    ManaSource,
    auto_plan_payment,
    parsed_cost,
)
from .mana_activation import complete_mana_activation, complete_mana_plan_activations
from .mana_ability_runtime import (
    mana_modes_for_ability,
    mana_output_for_ability,
)
from .mana_source_discovery import available_mana_sources
from .mana_undo import (
    clear_mana_undo_stack,
    priority_actions_with_mana_undo,
)
from .tap_state import tap_declared_attackers
from .stack_counter import (
    counter_stack_item,
    stack_item_can_be_countered,
)
from .stack_resolution import (
    complete_stack_resolution,
    trusted_generic_empty_resolution,
)
from .model import (
    CardInstance,
    CombatState,
    Event,
    GameConfig,
    GameState,
    GoadDesignation,
    PlayerState,
    StackItem,
    TurnEntry,
    TurnHistory,
    TurnHistoryEvent,
    TurnHistoryEventKind,
    YieldPolicy,
)
from .turn_history import opponent_was_dealt_damage_this_turn
from .object_query import exact_numeric_characteristic
from .permissions import AuthorizedCommand, CapabilityManager, PermissionDenied
from .protection import (
    ProtectionSource,
    ProtectionVerdict,
    protection_verdict,
    protection_verdict_for_ref,
    source_characteristics_for_ref,
)
from .target_protection import TargetProtectionVerdict
from .target_protection_engine_adapter import (
    target_protection_verdict_for_row,
)
from .replacement_decisions import (
    apply_effect_with_replacement_choice,
    complete_replacement_order_choice,
    issue_combat_damage_replacement_choice,
)
from .replacement_effects import ReplacementChoiceRequired
from .replacement.immutable import FrozenMap, thaw_value
from .rules.casting import (
    build_cast_proposal,
    CastProposalError,
    CastProposalRequest,
    commit_cast,
)
from .rules.activation import (
    ActivationProposalError,
    ActivationProposalRequest,
    activation_condition_status,
    activation_availability,
    activated_abilities,
    build_activation_proposal,
    builtin_activation_resolution,
    commit_activation,
    is_builtin_activation_semantic,
)
from .rules.action_catalog import build_priority_action_catalog
from .semantics import SemanticProgram, SemanticRegistry
from .semantic_runtime.action_permissions import (
    ActionPermissionKind,
    controller_has_action_permission,
)
from .semantic_runtime.casting_activation_metadata import (
    active_loyalty_cost_modifiers,
)
from .semantic_runtime.combat_metadata import active_goad_prohibitions
from .semantic_runtime import (
    AddManaIntent,
    AddSubtypeIntent,
    ChooseOneRestBottomRandomIntent,
    CounterStackIntent,
    CopyControlledTokensIntent,
    CopyStackItemIntent,
    CreateTokenIntent,
    EliminatePlayersIntent,
    IntentPlan,
    LifeChangeIntent,
    MoveObjectsSimultaneouslyIntent,
    MoveLibraryCardsToBottomIntent,
    PayManaCostIntent,
    PayLifeIntent,
    PlaceCountersIntent,
    RecordChoiceIntent,
    RecordZoneMoveIntent,
    ReturnCardsToLibraryTopIntent,
    ReorderLibraryTopIntent,
    RetargetStackItemIntent,
    RevealLibraryCardsIntent,
    SemanticNodeError,
    semantic_source_context,
    SetCardDesignationIntent,
    ShuffleLibraryIntent,
    ZoneMoveIntent,
    ProliferateIntent,
    default_semantic_interpreter,
    execute_intent_plan,
    draw_resolution_batch,
    PreparedZoneChange,
    prepare_draw_resolution,
    typed_entry_life_payment_amount,
)
from .semantic_choices import (
    ChoiceObjectView,
    ChoiceStackView,
    SemanticChoiceContext,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoiceFrame,
    SnapshotSemanticChoiceQuery,
    default_semantic_choice_registry,
)
from .card_overrides import normalize_game_record_v3_effect
from .semantic_choices.engine_coordination import (
    SemanticChoiceCoordinationMixin,
)
from .semantic_choices.intent_host import SemanticChoiceIntentHostMixin
from .semantic_runtime.values import resolve_semantic_value
from .state_based_actions import (
    ObjectSnapshot,
    PermanentSnapshot,
    evaluate_state_based_actions,
    player_loss_seats,
)
from .state_based_execution import (
    commit_state_based_counter_removals,
    commit_state_based_zone_changes,
    prepare_state_based_execution,
)
from .targets import (
    TargetGroup,
    TargetPlan,
    available_modes,
    mode_effects,
    target_plan,
)
from .target_characteristics import TargetCharacteristicSnapshot
from .target_predicates import (
    TargetPredicateError,
    target_predicate_matches,
)
from .relative_power_target import (
    pin_host_relative_power_source_departures,
)
from .token_creation import TokenCreationError, create_tokens
from .util import (
    mana_cost_to_vector,
    normalize_mana_bundle,
    parse_mana_symbols,
    pay_mana_from_pool,
    stable_json,
    unique_preserving_order,
)

PUBLIC_ZONES = {"battlefield", "graveyard", "exile", "command", "stack"}
HIDDEN_ZONES = {"hand", "library"}


def _stack_mode_effects(
    target_schema: Mapping[str, Any],
    item: StackItem,
) -> list[dict[str, Any]]:
    return mode_effects(
        target_schema,
        item.modes,
        target_groups=(
            item.context.get("target_groups_current")
            or item.context.get("target_groups")
            or {}
        ),
    )


@dataclass(slots=True)
class ActionResult:
    ok: bool
    summary: str
    event_ids: list[int]
    state_changed: bool = True
    warnings: list[str] | None = None


class CommanderEngine(
    AbilityFragmentHostMixin,
    CharacteristicEvaluationHostMixin,
    CastingCostHostMixin,
    TriggerProcessingHostMixin,
    TargetSelectionOwnerMixin,
    HiddenSearchOwnerMixin,
    ApnapChoiceOwnerMixin,
    StormTargetChoiceOwnerMixin,
    OneShotExileCastChoiceOwnerMixin,
    PublicChoiceOwnerMixin,
    SemanticChoiceCoordinationMixin,
    SemanticChoiceIntentHostMixin,
):
    """Authoritative multiplayer Commander kernel.

    Pilots receive capability-scoped strategic decisions.  Card-text resolution
    is a separate arbiter role and may be cached as generic semantic programs.
    The split is deliberate: a future graphical/network client can authenticate
    seats and route the same command envelopes without granting players direct
    mutation access to game state.
    """

    def __init__(
        self,
        card_db: CardDatabase,
        state: GameState,
        semantics: SemanticRegistry | None = None,
    ):
        self.card_db = card_db
        self.state = state
        self.semantics = semantics or SemanticRegistry()
        self.permissions = CapabilityManager(self.state)
        self.turn_priority = TurnPriorityDecisionOwner(self)
        self.turn_steps = TurnStepOwner(self)
        self._semantic_trust_cache: dict[tuple[str, str, str], bool] = {}
        self._current_semantic_trust_cache: dict[
            tuple[int, tuple[tuple[str, int], ...], str], bool
        ] = {}
        self._assert_invariants()

    def semantic_program_is_current_trusted(
        self,
        program: SemanticProgram | None,
    ) -> bool:
        if program is None or program.trust_level != "trusted":
            return False
        current_programs = self.semantics.programs_for_oracle(
            program.oracle_id or ""
        )
        current_signature = tuple(
            sorted(
                (candidate.key, id(candidate))
                for candidate in current_programs
            )
        )
        current_cache_key = (
            id(self.semantics),
            current_signature,
            program.key,
        )
        current_program = next(
            (
                candidate
                for candidate in current_programs
                if candidate.key == program.key
            ),
            None,
        )
        if current_program is program:
            cached = self._current_semantic_trust_cache.get(current_cache_key)
            if cached is not None:
                return cached
        program_hash = hashlib.sha256(
            stable_json(program.to_dict()).encode("utf-8")
        ).hexdigest()
        card_fingerprint = canonical_program_fingerprint(
            self.semantics, program
        )
        if card_fingerprint is None:
            if not self.semantics.is_runtime_handler_compatibility_program(
                program
            ):
                return False
            card_fingerprint = f"runtime-compatibility:{program.key}"
        cache_key = (program.key, program_hash, card_fingerprint)
        cached = self._semantic_trust_cache.get(cache_key)
        if cached is not None:
            if current_program is program:
                self._current_semantic_trust_cache[current_cache_key] = cached
            return cached
        result = program_source_is_current(self.card_db, program)
        self._semantic_trust_cache[cache_key] = result
        if current_program is program:
            self._current_semantic_trust_cache[current_cache_key] = result
        return result

    # ------------------------------------------------------------------
    # Construction, persistence, and transactions
    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        card_db: CardDatabase,
        decks: Mapping[str, DeckDefinition],
        *,
        first_player: str | None = None,
        player_names: Mapping[str, str] | None = None,
        config: GameConfig | None = None,
        semantics: SemanticRegistry | None = None,
    ) -> "CommanderEngine":
        state = initial_commander_state(
            card_db,
            decks,
            first_player=first_player,
            player_names=player_names,
            config=config, semantics=semantics,
        )
        engine = cls(card_db, state, semantics)
        engine._log(
            None,
            "game.created",
            f"Created {len(state.turn_order)}-player Commander game; "
            f"{state.turn_order[0]} starts.",
            {
                "decks": state.deck_names,
                "turn_order": state.turn_order,
                "seed": state.config.seed,
            },
            importance=3,
        )
        for seat in state.turn_order:
            engine.draw(
                seat,
                state.config.opening_hand_size,
                reason="opening hand",
                private=True,
            )
        engine._issue_mulligan_declaration()
        return engine

    @classmethod
    def load(
        cls,
        card_db: CardDatabase,
        path: str,
        semantics: SemanticRegistry | None = None,
    ) -> "CommanderEngine":
        return cls(card_db, GameState.load(path), semantics)

    def save(self, path: str) -> None:
        self.state.save(path)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        snapshot = copy.deepcopy(self.state)
        try:
            yield
            self._assert_invariants()
        except Exception:
            self.state = snapshot
            self.permissions = CapabilityManager(self.state)
            raise

    # ------------------------------------------------------------------
    # Basic state helpers
    # ------------------------------------------------------------------
    @property
    def seats(self) -> tuple[str, ...]:
        return tuple(self.state.turn_order)

    @property
    def active_seats(self) -> list[str]:
        return self.state.active_seats()

    def _record_turn_history(
        self,
        kind: TurnHistoryEventKind,
        *,
        actor: str | None = None,
        object_incarnation: str | None = None,
        target: str | None = None,
        target_kind: str | None = None,
        types: Iterable[str] = (),
        amount: int = 0,
    ) -> None:
        """Append one authoritative current-turn look-back fact.

        Legacy Game Record v3 checkpoints omit ``turn_history``.  They keep
        that feature disabled so loading and reserializing one cannot silently
        add a hashed rules field partway through its command replay.
        """

        history = self.state.turn_history
        if history is None:
            return
        if history.turn_sequence != self.state.turn_sequence:
            history = TurnHistory(turn_sequence=self.state.turn_sequence)
            self.state.turn_history = history
        history.events.append(
            TurnHistoryEvent(
                kind=kind,
                actor=actor,
                object_incarnation=object_incarnation,
                target=target,
                target_kind=target_kind,
                types=tuple(sorted({str(value).casefold() for value in types})),
                amount=max(0, int(amount)),
            )
        )

    def _current_turn_history(
        self,
        kind: TurnHistoryEventKind,
    ) -> tuple[TurnHistoryEvent, ...]:
        history = self.state.turn_history
        if (
            history is None
            or history.schema_version != 1
            or history.turn_sequence != self.state.turn_sequence
        ):
            return ()
        return tuple(event for event in history.events if event.kind == kind)

    def _player_cast_spell_this_turn(
        self,
        player: str,
        *,
        creature: bool | None = None,
    ) -> bool:
        for event in self._current_turn_history("spell_cast"):
            if event.actor != player:
                continue
            is_creature = "creature" in event.types
            if creature is None or is_creature == creature:
                return True
        return False

    def _creature_died_under_control_this_turn(self, player: str) -> bool:
        return any(
            event.actor == player
            for event in self._current_turn_history("creature_died")
        )

    def _object_attacked_player_this_turn(
        self,
        object_incarnation: str,
        player: str,
    ) -> bool:
        return any(
            event.object_incarnation == object_incarnation
            and event.target_kind == "player"
            and event.target == player
            for event in self._current_turn_history("creature_attacked")
        )

    def _all_visibility(self) -> list[str]:
        return [*self.seats, "arbiter", "analyst", "spectator"]

    def _require_seat(self, seat: str, *, in_game: bool = False) -> None:
        if seat not in self.state.players:
            raise GameRuleError(f"Unknown seat {seat!r}")
        if in_game and not self.state.players[seat].in_game:
            raise GameRuleError(f"{seat} is no longer in the game")

    def _next_ref(self, prefix: str) -> str:
        self.state.ref_counters[prefix] = self.state.ref_counters.get(prefix, 0) + 1
        return f"{prefix}{self.state.ref_counters[prefix]}"

    def _stable_runtime_id(self, kind: str, ref: str) -> str:
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"mtg-commander-sim:{self.state.game_id}:{kind}:{ref}",
        ).hex

    def _next_zone_timestamp(self) -> int:
        """Compatibility facade for the zone-transition timestamp owner."""

        return ZoneTransitionOwner(self).next_timestamp()

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
    ) -> Event:
        self.state.event_sequence += 1
        event = Event(
            event_id=self.state.event_sequence,
            revision=self.state.revision,
            turn_sequence=self.state.turn_sequence,
            active_player=self.state.active_player,
            phase=self.state.phase,
            step=self.state.step,
            actor=actor,
            code=code,
            summary=summary,
            details=dict(details or {}),
            visibility=list(visibility or self._all_visibility()),
            importance=importance,
            changed_objects=list(changed_objects),
            changed_players=list(changed_players),
        )
        self.state.events.append(event)
        self._update_yield_change_epochs(event)
        return event

    def _yield_change_epoch(
        self,
        kind: str,
        seat: str | None = None,
    ) -> int:
        return self.turn_priority.yield_change_epoch(kind, seat)

    def _increment_yield_change_epoch(
        self,
        kind: str,
        seat: str | None = None,
    ) -> None:
        self.turn_priority.increment_yield_change_epoch(kind, seat)

    def _update_yield_change_epochs(self, event: Event) -> None:
        """Persist yield-invalidating changes independently of trace output.

        Standard Game Records intentionally omit some low-level events. Yield
        correctness therefore cannot depend on rescanning the in-memory event
        list after a save/load boundary.
        """

        self.turn_priority.update_yield_change_epochs(event)

    def become_monarch(self, seat: str, *, reason: str) -> str:
        """Make one active player the monarch under CR 725.1 and 725.3."""

        self._require_seat(seat, in_game=True)
        previous = self.state.monarch
        if previous == seat:
            return seat
        self.state.monarch = seat
        self._log(
            seat,
            "monarch.change",
            f"{seat} became the monarch.",
            {
                "player": seat,
                "previous": previous,
                "reason": reason,
            },
            importance=2,
            changed_players=unique_preserving_order(
                [value for value in (previous, seat) if value is not None]
            ),
        )
        return seat

    def _monarch_trigger(
        self,
        *,
        controller: str,
        label: str,
        effects: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any],
    ) -> StackItem:
        """Materialize one CR 725.2 inherent triggered ability."""

        ref = self._next_ref("S")
        return StackItem(
            stack_id=self._stable_runtime_id("stack", ref),
            ref=ref,
            kind="triggered_ability",
            controller=controller,
            label=label,
            visibility=list(self.seats),
            context={
                **copy.deepcopy(dict(context)),
                "dynamic_effects": copy.deepcopy(
                    [dict(effect) for effect in effects]
                ),
            },
        )

    def _assert_invariants(self) -> None:
        membership: dict[str, list[tuple[str, str]]] = {}
        for seat, player in self.state.players.items():
            for zone, ids in player.zones.items():
                if len(ids) != len(set(ids)):
                    raise StateInvariantError(f"Duplicate object in {seat}/{zone}")
                for object_id in ids:
                    if object_id not in self.state.cards:
                        raise StateInvariantError(f"Unknown object {object_id} in {seat}/{zone}")
                    membership.setdefault(object_id, []).append((seat, zone))
        stack_cards = {
            item.card_object_id
            for item in self.state.stack
            if item.card_object_id
        }
        for item in self.state.stack:
            object_id = item.card_object_id
            if not object_id:
                continue
            if (
                object_id not in self.state.cards
                or (
                    self.state.cards[object_id].zone != "stack"
                    and not item.context.get("currently_resolving")
                )
            ):
                raise StateInvariantError(
                    f"Stack item references nonstack object {object_id}"
                )
        for object_id, card in self.state.cards.items():
            locations = membership.get(object_id, [])
            goading_players = [
                designation.player for designation in card.goaded_by
            ]
            if card.goaded_by and card.zone != "battlefield":
                raise StateInvariantError(
                    f"Nonbattlefield object {card.ref} is still goaded"
                )
            if len(goading_players) != len(set(goading_players)):
                raise StateInvariantError(
                    f"{card.ref} has duplicate goad designations"
                )
            if any(
                player not in self.state.players
                for player in goading_players
            ):
                raise StateInvariantError(
                    f"{card.ref} has a goad designation from an unknown player"
                )
            if card.zone == "stack":
                if object_id not in stack_cards or locations:
                    raise StateInvariantError(f"Invalid stack membership for {card.ref}")
            elif card.zone == "outside":
                if locations:
                    raise StateInvariantError(f"Outside-game object {card.ref} still appears in a zone")
            elif len(locations) != 1:
                raise StateInvariantError(f"{card.ref} appears in {locations}, expected exactly one zone")
            elif locations[0][1] != card.zone:
                raise StateInvariantError(f"{card.ref} zone mismatch {card.zone}/{locations[0]}")
            elif (
                card.zone == "battlefield"
                and locations[0][0] != card.controller
            ):
                raise StateInvariantError(
                    f"{card.ref} is indexed under {locations[0][0]} "
                    f"but controlled by {card.controller}"
                )
            elif (
                card.zone
                in {"library", "hand", "graveyard", "exile", "command"}
                and locations[0][0] != card.owner
            ):
                raise StateInvariantError(
                    f"{card.ref} is indexed under {locations[0][0]} "
                    f"but owned by {card.owner}"
                )
        if self.state.priority_player is not None and self.state.priority_player not in self.active_seats:
            raise StateInvariantError("Priority belongs to a player who is not in the game")
        if (
            self.state.monarch is not None
            and self.state.monarch not in self.active_seats
        ):
            raise StateInvariantError(
                "The monarch designation belongs to a player who is not in the game"
            )
        history = self.state.turn_history
        if history is not None:
            if history.schema_version != 1:
                raise StateInvariantError(
                    f"Unsupported turn-history schema {history.schema_version}"
                )
            # An empty journal carries no look-back facts, so direct fixture
            # setup may advance ``turn_sequence`` before the first writer
            # initializes it. A nonempty journal must never cross that
            # boundary because prior-turn facts would affect this turn.
            if (
                history.events
                and history.turn_sequence != self.state.turn_sequence
            ):
                raise StateInvariantError(
                    "Turn history does not belong to the current turn"
                )
            for event in history.events:
                if event.actor is not None and event.actor not in self.state.players:
                    raise StateInvariantError(
                        f"Turn history names unknown actor {event.actor}"
                    )
                if (
                    event.target_kind == "player"
                    and event.target not in self.state.players
                ):
                    raise StateInvariantError(
                        f"Turn history names unknown player target {event.target}"
                    )
        for player in self.state.players.values():
            if any(value < 0 for value in player.mana_pool.values()):
                raise StateInvariantError(f"Negative mana in {player.seat}'s pool")

    def card_record(self, value: str | CardInstance) -> CardRecord | None:
        card = value if isinstance(value, CardInstance) else self.state.cards[value]
        if card.oracle_id.startswith(
            ("custom-token:", "custom-copy:", "custom-emblem:")
        ):
            return None
        return self.card_db.by_oracle_id(card.oracle_id)

    def display_name(self, object_id: str) -> str:
        return str(self._effective_card_data(object_id).get("name") or self.state.cards[object_id].printed_name)

    def _copyable_characteristics(
        self, card: CardInstance
    ) -> dict[str, Any]:
        record = self.card_record(card)
        base = self._compiled_base_characteristics(
            card,
            record,
            error_type=GameRuleError,
        )
        base.update(
            copy.deepcopy(dict(card.annotations.get("copy_overrides") or {}))
        )
        return base

    def _resolve_object(
        self,
        seat: str,
        value: str,
        *,
        zones: Iterable[str] | None = None,
        controlled_only: bool = False,
        owned_only: bool = False,
    ) -> CardInstance:
        self._require_seat(seat)
        zone_filter = set(zones) if zones is not None else None
        if value in self.state.cards:
            card = self.state.cards[value]
            candidates = [card]
        else:
            normalized = value.casefold().strip()
            candidates = [
                card
                for card in self.state.cards.values()
                if card.ref.casefold() == normalized
                or card.printed_name.casefold() == normalized
                or self.display_name(card.object_id).casefold() == normalized
            ]
        filtered: list[CardInstance] = []
        for card in candidates:
            if card.zone == "outside":
                continue
            if zone_filter is not None and card.zone not in zone_filter:
                continue
            if controlled_only and card.controller != seat:
                continue
            if owned_only and card.owner != seat:
                continue
            filtered.append(card)
        if not filtered:
            raise GameRuleError(f"Could not find {value!r} for {seat} in requested zones")
        if len(filtered) > 1:
            options = ", ".join(f"{card.ref}:{card.zone}" for card in filtered)
            raise GameRuleError(f"Ambiguous object {value!r}; use a ref: {options}")
        return filtered[0]

    def _next_active_after(self, seat: str) -> str:
        active = self.active_seats
        if not active:
            raise GameRuleError("No active players remain")
        if seat not in self.state.turn_order:
            return active[0]
        index = self.state.turn_order.index(seat)
        for offset in range(1, len(self.state.turn_order) + 1):
            candidate = self.state.turn_order[(index + offset) % len(self.state.turn_order)]
            if self.state.players[candidate].in_game:
                return candidate
        return active[0]

    def apnap_order(self) -> list[str]:
        if not self.active_seats:
            return []
        start = self.state.active_player if self.state.active_player in self.active_seats else self.active_seats[0]
        result = [start]
        while len(result) < len(self.active_seats):
            nxt = self._next_active_after(result[-1])
            if nxt in result:
                break
            result.append(nxt)
        return result

    # ------------------------------------------------------------------
    # Zone movement, draw, and knowledge
    # ------------------------------------------------------------------
    def _remove_from_zone(self, card: CardInstance) -> None:
        """Compatibility facade for canonical zone membership mutation."""

        ZoneTransitionOwner(self).remove_from_zone(card)

    def _reset_zone_change(
        self,
        card: CardInstance,
        destination: str,
        *,
        zone_timestamp: int | None = None,
    ) -> None:
        """Compatibility facade for CR 400.7 incarnation reset."""

        ZoneTransitionOwner(self).reset_zone_change(
            card,
            destination,
            zone_timestamp=zone_timestamp,
        )
    @staticmethod
    def _trigger_item_matches_incarnation(
        card: CardInstance,
        item: StackItem | Mapping[str, Any],
    ) -> bool:
        """Whether one pending trigger was sourced by this exact object."""

        if isinstance(item, StackItem):
            source_object_id = item.source_object_id
            kind = item.kind
            context = item.context
        else:
            source_object_id = item.get("source_object_id")
            kind = str(item.get("kind") or "")
            context = dict(item.get("context") or {})
        if (
            source_object_id != card.object_id
            or "triggered" not in str(kind).casefold()
        ):
            return False
        source_incarnation = context.get("source_logical_object_id")
        return (
            source_incarnation is None
            or str(source_incarnation) == card.logical_object_id
        )

    def _battle_trigger_pending(self, card: CardInstance) -> bool:
        if any(
            self._trigger_item_matches_incarnation(card, item)
            for item in self.state.stack
        ):
            return True
        return any(
            self._trigger_item_matches_incarnation(card, item)
            for batch in self.state.pending_trigger_batches
            for group in batch.get("groups", [])
            for item in group.get("items", [])
        )

    def _queue_siege_defeated_trigger(
        self,
        battle: CardInstance,
    ) -> None:
        """Queue the intrinsic Siege trigger after its last defense counter."""

        if (
            battle.zone != "battlefield"
            or battle.phased_out
            or battle.controller not in self.active_seats
        ):
            return
        _, subtypes, _ = self._type_parts(
            str(
                self._effective_card_data(battle).get("type_line")
                or ""
            )
        )
        if "siege" not in subtypes:
            return
        pending_items: list[StackItem | Mapping[str, Any]] = [
            *self.state.stack,
            *[
                item
                for batch in self.state.pending_trigger_batches
                for group in batch.get("groups", [])
                for item in group.get("items", [])
            ],
        ]
        if any(
            self._trigger_item_matches_incarnation(battle, item)
            and (
                item.semantic_key
                if isinstance(item, StackItem)
                else item.get("semantic_key")
            )
            == "builtin:siege-defeated"
            for item in pending_items
        ):
            return
        ref = self._next_ref("S")
        enqueue_trigger_batch(self, [
                StackItem(
                    stack_id=self._stable_runtime_id("stack", ref),
                    ref=ref,
                    kind="triggered_ability",
                    controller=battle.controller,
                    label=f"{self.display_name(battle.object_id)} defeated",
                    source_object_id=battle.object_id,
                    semantic_key="builtin:siege-defeated",
                    visibility=list(self.seats),
                    context={
                        "event": "battle.last_defense_removed",
                        "battle": battle.ref,
                        "source_logical_object_id": (
                            battle.logical_object_id
                        ),
                        "native_transformed_cast": True,
                    },
                )
            ]
        )

    def move_card(
        self,
        object_id: str,
        destination: str,
        *,
        controller: str | None = None,
        tapped: bool | None = None,
        entry_pay_life: bool = False,
        enter_face: str | None = None,
        battle_protector: str | None = None,
        aura_target_ref: str | None = None,
        resolving_as_aura_spell: bool = False,
        aura_enchant_spec: EnchantSpec | None = None,
        zone_timestamp: int | None = None,
        position: str | int = "top",
        reveal_to: Iterable[str] | None = None,
        reason: str = "",
        log: bool = True,
        semantic_events: bool = False,
        replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
        prepared_replacement: PreparedZoneChange | None = None,
        transition_kind: ZoneTransitionKind = ZoneTransitionKind.ORDINARY,
        _characteristic_lki_prepared: bool = False,
    ) -> CardInstance:
        """Compatibility facade for the canonical zone-transition owner."""

        return ZoneTransitionOwner(self).move_card(
            object_id,
            destination,
            controller=controller,
            tapped=tapped,
            entry_pay_life=entry_pay_life,
            enter_face=enter_face,
            battle_protector=battle_protector,
            aura_target_ref=aura_target_ref,
            resolving_as_aura_spell=resolving_as_aura_spell,
            aura_enchant_spec=aura_enchant_spec,
            zone_timestamp=zone_timestamp,
            position=position,
            reveal_to=reveal_to,
            reason=reason,
            log=log,
            semantic_events=semantic_events,
            replacement_selections=replacement_selections,
            prepared_replacement=prepared_replacement,
            transition_kind=transition_kind,
            characteristic_lki_prepared=_characteristic_lki_prepared,
        )

    @staticmethod
    def _library_insertion_index(
        library_size: int,
        position: str | int | None,
    ) -> int:
        return ZoneTransitionOwner.library_insertion_index(
            library_size,
            position,
        )

    def _semantic_event_sources(
        self,
        *,
        zones: set[str] | None = None,
    ) -> list[CardInstance]:
        return ZoneTransitionOwner(self).semantic_event_sources(zones=zones)

    def _dispatch_zone_change_events(
        self,
        card: CardInstance,
        *,
        origin: str,
        destination: str | None,
        origin_controller: str,
        origin_logical_object_id: str,
        origin_data: Mapping[str, Any],
        origin_attachments: Sequence[str],
        origin_attached_to: str | None = None,
        departure_sources: Sequence[CardInstance],
        departure_source_zones: Mapping[str, str],
        departure_source_characteristics: Mapping[
            str, Mapping[str, Any]
        ],
        reason: str,
        transition_kind: ZoneTransitionKind = ZoneTransitionKind.ORDINARY, read_ahead_chapter: int | None = None,
        trigger_batch: list[StackItem] | None = None,
    ) -> None:
        """Game Record v3 compatibility facade for normalized zone events."""

        occurrence, event_triggers, owns_trigger_batch = (
            ZoneTransitionOwner(self).dispatch_zone_change_events(
                card,
                departure=ZoneDepartureSnapshot(
                    origin=origin,
                    controller=origin_controller,
                    logical_object_id=origin_logical_object_id,
                    characteristics=origin_data,
                    attachments=tuple(origin_attachments),
                    attached_to=origin_attached_to,
                    trigger_sources=DepartureTriggerSnapshot(
                        sources=tuple(departure_sources),
                        source_zones=dict(departure_source_zones),
                        source_characteristics=dict(
                            departure_source_characteristics
                        ),
                    ),
                ),
                destination=destination,
                reason=reason,
                transition_kind=transition_kind, read_ahead_chapter=read_ahead_chapter,
                trigger_batch=trigger_batch,
            )
        )
        origin_types, _, _ = self._type_parts(
            str(occurrence.previous_characteristics.get("type_line") or "")
        )
        if not (
            occurrence.origin == "battlefield"
            and occurrence.destination == "graveyard"
            and "artifact" in origin_types
            and card.is_card_object
            and card.owner in self.active_seats
        ):
            if owns_trigger_batch:
                enqueue_trigger_batch(self, event_triggers)
            return
        emblem_owner = self.state.players[card.owner]
        emblem_sources: list[CardInstance | None] = [
            self.state.cards[object_id]
            for object_id in emblem_owner.zones["command"]
            if (
                self.state.cards[object_id].object_kind == "emblem"
                and self.state.cards[object_id].annotations.get(
                    "emblem_semantic_key"
                )
                == "builtin:daretti-emblem"
            )
        ]
        if not emblem_owner.stats.get("emblem_objects_v1"):
            emblem_sources.extend(
                [None] * int(emblem_owner.stats.get("daretti_emblems", 0))
            )
        for emblem in emblem_sources:
            ref = self._next_ref("S")
            event_triggers.append(
                StackItem(
                    stack_id=self._stable_runtime_id("stack", ref),
                    ref=ref,
                    kind="triggered_ability",
                    controller=card.owner,
                    label=(
                        "Daretti emblem — return artifact at the next end step"
                    ),
                    semantic_key="builtin:daretti-emblem",
                    source_object_id=(
                        emblem.object_id if emblem is not None else None
                    ),
                    visibility=list(self.seats),
                    context={
                        "event": "artifact.graveyard",
                        "card": card.ref,
                        "card_zone_change_counter": card.zone_change_counter,
                    },
                )
            )
        if owns_trigger_batch:
            enqueue_trigger_batch(self, event_triggers)

    def _move_cards_simultaneously(
        self,
        changes: Sequence[tuple[str, str]],
        *,
        reason: str,
        log: bool = False,
        replacement_selections: Sequence[
            str | None | Mapping[str, Any]
        ] = (),
        transition_kinds: Mapping[
            str, ZoneTransitionKind
        ] | None = None,
    ) -> list[CardInstance]:
        """Compatibility facade for one simultaneous zone transaction."""

        return ZoneTransitionOwner(self).move_cards_simultaneously(
            changes,
            reason=reason,
            log=log,
            replacement_selections=replacement_selections,
            transition_kinds=transition_kinds,
        )

    def shuffle_library(self, seat: str, *, reason: str = "shuffle") -> None:
        """Compatibility facade for canonical library membership mutation."""

        ZoneTransitionOwner(self).shuffle_library(seat, reason=reason)

    def draw(
        self,
        seat: str,
        count: int = 1,
        *,
        reason: str = "draw",
        private: bool = False,
    ) -> list[str]:
        """Commit setup or explicitly unreplaced draws through CR 121 state.

        In-game instructions use ``_begin_draw_sequence`` so every individual
        draw is offered to the replacement pipeline before this commit owner is
        reached.  Opening hands and mulligan redraws are not game draws.
        """

        try:
            return list(
                commit_unreplaced_draws(
                    self,
                    seat,
                    count,
                    reason=reason,
                    private=private,
                )
            )
        except DrawError as exc:
            raise GameRuleError(str(exc)) from exc

    def _begin_draw_sequence(
        self,
        seat: str,
        count: int,
        *,
        reason: str,
        private: bool = False,
        continuation: Mapping[str, Any] | None = None,
        post_draw_actions: tuple[DrawnCardAction, ...] = (),
    ) -> None:
        """Resolve one draw instruction, then each draw independently."""

        try:
            begin_draw_sequence(
                self,
                seat,
                count,
                reason=reason,
                private=private,
                continuation=continuation,
                post_draw_actions=post_draw_actions,
            )
        except DrawError as exc:
            raise GameRuleError(str(exc)) from exc

    def _complete_draw_replacement(self, decision: Any) -> None:
        try:
            complete_draw_decision(self, decision)
        except DrawError as exc:
            raise GameRuleError(str(exc)) from exc

    def _resume_after_draw(
        self,
        continuation: Mapping[str, Any],
    ) -> None:
        try:
            resume_after_draw(self, continuation)
        except DrawError as exc:
            raise GameRuleError(str(exc)) from exc

    def _complete_draw_step_entry(self, active: str) -> None:
        """Put draw-step triggers on the stack only after the turn draw.

        CR 504.1's draw is a turn-based action.  Beginning-of-draw-step
        triggers have already triggered, but CR 504.2 does not put them on
        the stack or grant priority until after that action and the ensuing
        state-based-action check.  Collect semantic and delayed triggers into
        one APNAP/order batch so neither source kind can preempt the draw.
        """

        context = {
            "phase": self.state.phase,
            "step": self.state.step,
            "player": active,
        }
        trigger_batch = collect_trigger_items(
            self,
            "step.begin",
            context,
        )
        if self._semantic_pause_annotation() is not None:
            return
        enqueue_trigger_batch(self, trigger_batch)
        if self._stabilize():
            return
        self._grant_priority(active)

    # ------------------------------------------------------------------
    # Capability-scoped command entry point
    # ------------------------------------------------------------------
    def submit(
        self,
        *,
        token: str,
        principal: str,
        action: str,
        payload: Mapping[str, Any] | None = None,
    ) -> ActionResult:
        payload_dict = dict(payload or {})
        start_event = self.state.event_sequence
        with self.transaction():
            authorized = self.permissions.authorize(
                token=token,
                principal=principal,
                action=action,
                payload=payload_dict,
            )
            self.state.revision += 1
            self.permissions.record_response(authorized)
            actor = authorized.capability.actor
            self._log(
                actor,
                "decision.response",
                f"{principal} submitted {action} for {authorized.decision.kind}.",
                {"decision": authorized.decision.decision_id, "action": action},
                visibility=[actor, "analyst"] if actor else [principal, "analyst"],
                importance=0,
                changed_players=[actor] if actor else [],
            )
            if self.permissions.decision_complete():
                decision = self.permissions.close_decision()
                self._dispatch_completed_decision(decision)
            self.pump()
        return ActionResult(
            True,
            f"Accepted {action}",
            list(range(start_event + 1, self.state.event_sequence + 1)),
        )

    def try_submit(self, **kwargs: Any) -> ActionResult:
        try:
            return self.submit(**kwargs)
        except (GameRuleError, PermissionDenied, ValueError, ManaPlanError) as exc:
            return ActionResult(False, str(exc), [], state_changed=False, warnings=["State was rolled back."])

    def _dispatch_completed_decision(self, decision: Any) -> None:
        kind = decision.kind
        if kind == "mulligan.declare":
            self._complete_mulligan_declaration(decision)
        elif kind == "mulligan.bottom":
            self._complete_mulligan_bottom(decision)
        elif kind == "priority":
            self._complete_priority(decision)
        elif kind == "combat.attackers":
            self._complete_attackers(decision)
        elif kind == "combat.blockers":
            self._complete_blockers(decision)
        elif kind == "combat.damage":
            self._complete_combat_damage(decision)
        elif kind == "cleanup.discard":
            self._complete_cleanup_discard(decision)
        elif kind == "state.legend":
            self._complete_legend_choice(decision)
        elif kind == "state.commander_zone":
            self._complete_commander_zone_choice(decision)
        elif kind == "state.battle_protector":
            self._complete_battle_protector_choice(decision)
        elif kind == "battle.enter_protector":
            self._complete_battle_entry_protector_choice(decision)
        elif kind == "battle.siege_defeated":
            self._complete_siege_defeated_choice(decision)
        elif kind == "selection.exile_cast":
            self._complete_one_shot_exile_cast_choice(decision)
        elif kind == "choice.apnap":
            self._complete_apnap_choice(decision)
        elif kind == "replacement.order":
            complete_replacement_order_choice(
                self,
                decision,
                error_type=GameRuleError,
            )
        elif kind == "aura.entry":
            complete_aura_entry_choice(
                self,
                decision,
                error_type=GameRuleError,
            )
        elif kind == "trigger.order":
            complete_trigger_order_decision(self, decision)
        elif kind == "arbiter.resolve":
            self._complete_arbiter_resolution(decision)
        elif kind == "search.fetch":
            self._complete_fetch_choice(decision)
        elif kind == "semantic.target":
            self._complete_semantic_target(decision)
        elif kind == "semantic.choice":
            self._complete_semantic_choice(decision)
        elif kind == "semantic.search":
            self._complete_semantic_search(decision)
        elif kind == "semantic.storm":
            self._complete_storm_choice(decision)
        elif kind in {"draw.replacement", "draw.reveal"}:
            self._complete_draw_replacement(decision)
        else:
            raise GameRuleError(f"Unsupported completed decision {kind}")

    # ------------------------------------------------------------------
    # Multiplayer London mulligan
    # ------------------------------------------------------------------
    def _opening_hand_signals(self, seat: str) -> dict[str, Any]:
        player = self.state.players[seat]
        lands = 0
        early_mana = 0
        colored_sources: set[str] = set()
        early_actions = 0
        for object_id in player.zones["hand"]:
            record = self.card_record(object_id)
            if not record:
                continue
            if record.is_land:
                lands += 1
                for ability in self._activated_abilities(
                    self.state.cards[object_id]
                ):
                    if not ability.mana_ability:
                        continue
                    for mode in ability.fixed_mana_outputs:
                        colored_sources.update(
                            color
                            for color, amount in mode.bundle.items()
                            if amount and color in "WUBRG"
                        )
            elif record.mana_value <= 2:
                early_actions += 1
                card = self.state.cards[object_id]
                has_compiled_mana = any(
                    ability.mana_ability
                    for ability in self._activated_abilities(card)
                )
                has_compiled_land_search = any(
                    effect.get("op") == "search"
                    for program in self.semantics.programs_for_oracle(
                        record.oracle_id,
                        event="resolve",
                    )
                    for effect in program.effects
                )
                if has_compiled_mana or has_compiled_land_search:
                    early_mana += 1
        commander_colors = sorted(self._commander_identity(seat))
        red_flags: list[str] = []
        if lands == 0:
            red_flags.append("no lands")
        elif lands == 1 and early_mana == 0:
            red_flags.append("one land and no cheap acceleration")
        if lands >= 6:
            red_flags.append("six or more lands")
        missing = [color for color in commander_colors if color not in colored_sources]
        if missing and lands <= 2 and early_mana == 0:
            red_flags.append("thin early color access: " + "".join(missing))
        functional = not red_flags and (2 <= lands <= 5 or (lands == 1 and early_mana >= 1))
        return {
            "lands": lands,
            "cheap_mana": early_mana,
            "other_early_actions": early_actions,
            "visible_source_colors": sorted(colored_sources),
            "commander_colors": commander_colors,
            "red_flags": red_flags,
            "functional_baseline": functional,
        }

    def _mulligan_hand_payload(self, seat: str) -> dict[str, Any]:
        player = self.state.players[seat]
        free = self.state.config.effective_free_mulligans(len(self.seats))
        next_mulligans = player.mulligans_taken + 1
        next_penalty = max(0, next_mulligans - free)
        after_free = player.mulligans_taken >= free
        return {
            "hand": [
                {"id": self.state.cards[oid].ref, "name": self.state.cards[oid].printed_name}
                for oid in player.zones["hand"]
            ],
            "hand_size": len(player.zones["hand"]),
            "mulligans_taken": player.mulligans_taken,
            "free_mulligans": free,
            "signals": self._opening_hand_signals(seat),
            "if_mulligan": {
                "draw": self.state.config.opening_hand_size,
                "bottom": next_penalty,
                "resulting_hand_size": self.state.config.opening_hand_size - next_penalty,
            },
            "decision_policy": (
                "KEEP any functional hand after the free redraw. Do not chase an ideal seven: "
                "rejecting this hand means selecting the next opener from seven and immediately "
                f"bottoming {next_penalty}, for a {self.state.config.opening_hand_size - next_penalty}-card keep."
                if after_free
                else "This is the multiplayer free-mulligan decision. Mulligan only for a materially better chance at a functional opener, not a perfect one."
            ),
        }

    def _issue_mulligan_declaration(
        self,
        *,
        actors: Sequence[str] | None = None,
        index: int = 0,
        mulliganers: Sequence[str] = (),
        round_no: int | None = None,
    ) -> None:
        """Issue the next declaration in turn order for one mulligan round.

        Rule 103.5 has players declare in turn order. Only after every eligible
        player has declared do all mulliganers redraw at the same time. Keeping
        is final, so later rounds contain only players who mulliganed.
        """

        if actors is None:
            actors = [seat for seat in self.state.turn_order if not self.state.players[seat].kept_hand]
            if not actors:
                self._start_game()
                return
            self.state.mulligan_round += 1
            round_no = self.state.mulligan_round
            self._log(
                None,
                "mulligan.round",
                f"Mulligan round {round_no} declarations opened in turn order.",
                {"actors": list(actors)},
                importance=1,
            )
        actor_list = list(actors)
        if round_no is None:
            round_no = self.state.mulligan_round
        if index >= len(actor_list):
            self._perform_mulligan_redraws(list(mulliganers))
            return

        seat = actor_list[index]
        self.permissions.issue(
            kind="mulligan.declare",
            role="pilot",
            actors=[seat],
            allowed_actions=["keep", "mulligan"],
            payload_by_actor={seat: self._mulligan_hand_payload(seat)},
            simultaneous=False,
            continuation={
                "round": round_no,
                "actors": actor_list,
                "index": index,
                "mulliganers": list(mulliganers),
            },
        )

    def _complete_mulligan_declaration(self, decision: Any) -> None:
        seat = decision.actors[0]
        response = decision.responses[seat]
        action = response["action"]
        player = self.state.players[seat]
        mulliganers = list(decision.continuation.get("mulliganers") or [])

        if action == "keep":
            player.kept_hand = True
            player.mulligan_status = "kept"
            self._log(
                seat,
                "mulligan.keep",
                f"{seat} kept {len(player.zones['hand'])} cards after {player.mulligans_taken} mulligan(s).",
                {"hand_size": len(player.zones["hand"]), "mulligans": player.mulligans_taken},
                importance=2,
                changed_players=[seat],
            )
            self._log(
                seat,
                "mulligan.keep.private",
                f"{seat} kept: {', '.join(self.state.cards[oid].printed_name for oid in player.zones['hand'])}.",
                {"objects": [self.state.cards[oid].ref for oid in player.zones["hand"]]},
                visibility=[seat, "analyst"],
                importance=1,
            )
        elif action == "mulligan":
            free = self.state.config.effective_free_mulligans(len(self.seats))
            signals = self._opening_hand_signals(seat)
            if (
                self.state.config.realistic_mulligan_guard
                and player.mulligans_taken >= free
                and signals.get("functional_baseline")
                and not str(response.get("override_reason") or "").strip()
            ):
                raise GameRuleError(
                    f"{seat}'s post-free hand meets the functional baseline. "
                    "Keep it, or resubmit mulligan with override_reason explaining why a six-card hand is preferable."
                )
            mulliganers.append(seat)
            self._log(
                seat,
                "mulligan.declare",
                f"{seat} declared a mulligan in round {decision.continuation.get('round')}.",
                {"round": decision.continuation.get("round")},
                importance=1,
            )
        else:
            raise GameRuleError(f"Invalid mulligan declaration {action}")

        actors = list(decision.continuation.get("actors") or [seat])
        next_index = int(decision.continuation.get("index", 0)) + 1
        self._issue_mulligan_declaration(
            actors=actors,
            index=next_index,
            mulliganers=mulliganers,
            round_no=int(decision.continuation.get("round") or self.state.mulligan_round),
        )

    def _perform_mulligan_redraws(self, mulliganers: list[str]) -> None:
        """Apply every declared mulligan before asking for private bottom choices."""

        free = self.state.config.effective_free_mulligans(len(self.seats))
        bottomers: list[str] = []
        for seat in mulliganers:
            player = self.state.players[seat]
            for object_id in list(player.zones["hand"]):
                self.move_card(object_id, "library", log=False)
            self.shuffle_library(seat, reason="mulligan")
            player.mulligans_taken += 1
            player.mulligan_penalty = max(0, player.mulligans_taken - free)
            self.draw(seat, self.state.config.opening_hand_size, reason="mulligan", private=True)
            player.mulligan_status = "bottoming" if player.mulligan_penalty else "pending"
            self._log(
                seat,
                "mulligan.redraw",
                f"{seat} redrew seven; penalty is {player.mulligan_penalty} bottom card(s).",
                {"mulligans": player.mulligans_taken, "bottom": player.mulligan_penalty},
                importance=2,
                changed_players=[seat],
            )
            if player.mulligan_penalty:
                bottomers.append(seat)

        if bottomers:
            self.permissions.issue(
                kind="mulligan.bottom",
                role="pilot",
                actors=bottomers,
                allowed_actions=["bottom"],
                payload_by_actor={
                    seat: {
                        "count": self.state.players[seat].mulligan_penalty,
                        "hand": [
                            {"id": self.state.cards[oid].ref, "name": self.state.cards[oid].printed_name}
                            for oid in self.state.players[seat].zones["hand"]
                        ],
                    }
                    for seat in bottomers
                },
                simultaneous=True,
            )
            return
        if all(player.kept_hand for player in self.state.players.values()):
            self._start_game()
        else:
            self._issue_mulligan_declaration()

    def _complete_mulligan_bottom(self, decision: Any) -> None:
        for seat in decision.actors:
            player = self.state.players[seat]
            response = decision.responses[seat]
            values = list(
                response.get("cards")
                or response.get("card_ids")
                or response.get("bottom")
                or []
            )
            required = player.mulligan_penalty
            if len(values) != required:
                raise GameRuleError(f"{seat} must bottom exactly {required} card(s)")
            resolved: list[str] = []
            for value in values:
                card = self._resolve_object(seat, str(value), zones={"hand"}, owned_only=True)
                if card.object_id in resolved:
                    raise GameRuleError("The same card cannot be bottomed twice")
                resolved.append(card.object_id)
            for object_id in resolved:
                self.move_card(object_id, "library", position="bottom", log=False)
            player.mulligan_status = "pending"
            self._log(
                seat,
                "mulligan.bottom",
                f"{seat} bottomed {required} card(s); current hand size {len(player.zones['hand'])}.",
                {"count": required},
                importance=2,
                changed_objects=resolved,
                changed_players=[seat],
            )
        self._issue_mulligan_declaration()

    # ------------------------------------------------------------------
    # Turn scheduler, delayed triggers, and priority
    # ------------------------------------------------------------------
    def _start_game(self) -> None:
        self.turn_steps.start_game()

    def schedule_extra_turn(self, seat: str, *, source: str | None = None) -> TurnEntry:
        return self.turn_steps.schedule_extra_turn(seat, source=source)

    def _next_normal_player(self) -> str:
        return self.turn_steps.next_normal_player()

    def _select_next_turn(self) -> TurnEntry:
        return self.turn_steps.select_next_turn()

    def _begin_turn(self, entry: TurnEntry) -> None:
        self.turn_steps.begin_turn(entry)

    def _expire_goad_designations(self, player: str) -> None:
        """Expire CR 701.15 designations at the goading player's turn."""

        turns_begun = self.state.players[player].turns_begun
        changed: list[str] = []
        for card in self.state.cards.values():
            retained = [
                designation
                for designation in card.goaded_by
                if not (
                    designation.player == player
                    and designation.expires_at_turns_begun <= turns_begun
                )
            ]
            if len(retained) != len(card.goaded_by):
                card.goaded_by = retained
                changed.append(card.object_id)
        if changed:
            self._log(
                player,
                "permanent.goad.expire",
                f"{len(changed)} goad designation(s) expired as {player}'s turn began.",
                {
                    "player": player,
                    "turns_begun": turns_begun,
                    "objects": [
                        self.state.cards[object_id].ref
                        for object_id in changed
                    ],
                },
                importance=1,
                changed_objects=changed,
            )

    def _clear_mana(self, *, reason: str) -> None:
        for seat, player in self.state.players.items():
            clear_mana_undo_stack(player.stats)
            if any(player.mana_pool.values()):
                lost = dict(player.mana_pool)
                player.mana_pool = normalize_mana_bundle(None)
                player.stats.pop("restricted_mana", None)
                self._log(seat, "mana.empty", f"{seat}'s mana pool emptied.", {"lost": lost, "reason": reason}, importance=0, changed_players=[seat])

    def _enter_step(
        self,
        *,
        held_triggers: Sequence[StackItem] = (),
        phase: str | None = None,
        step: str | None = None,
        active: str | None = None,
    ) -> None:
        if phase is None:
            if step is not None or active is not None:
                raise StateInvariantError(
                    "A step callback requires phase, step, and active player"
                )
            self.turn_steps.enter_step(held_triggers=held_triggers)
            return
        if step is None or active is None:
            raise StateInvariantError(
                "A step callback requires phase, step, and active player"
            )

        if step == "untap":
            untap_step_coordination.coordinate_untap_step(
                self,
                phase=phase,
                step=step,
                active_player=active,
                held_triggers=held_triggers,
            )
            return

        waiting_at_priority = turn_counter_coordination.coordinate_turn_counter_step(
            self, active, phase, step, held_triggers
        )
        if waiting_at_priority is None:
            return

        if step == "cleanup":
            # Abilities can trigger at the beginning of cleanup, but CR
            # 514.1-2 happen before those waiting triggers are put on the
            # stack and before the exceptional priority window.  Enqueue
            # represented semantic triggers now without stabilizing them.
            cleanup_triggers = collect_trigger_items(
                self,
                "step.begin",
                {"phase": phase, "step": step, "player": active},
            )
            enqueue_trigger_batch(self, cleanup_triggers)
            hand = self.state.players[active].zones["hand"]
            excess = (
                len(hand)
                - self.state.players[active].max_hand_size
            )
            if excess > 0:
                self.permissions.issue(
                    kind="cleanup.discard",
                    role="pilot",
                    actors=[active],
                    allowed_actions=["discard"],
                    payload_by_actor={
                        active: {
                            "count": excess,
                            "hand": [
                                {
                                    "id": self.state.cards[oid].ref,
                                    "name": self.state.cards[
                                        oid
                                    ].printed_name,
                                }
                                for oid in hand
                            ],
                        }
                    },
                )
                return
            self._finish_cleanup()
            return

        if step in {"beginning_combat", "end_step", "end_combat"}:
            # None of these supported-profile boundaries has a turn-based
            # choice. Collect both permanent-based and delayed beginning-of-
            # step triggers before granting priority. A delayed trigger must
            # not cause semantic event dispatch to be skipped.
            context = {
                "phase": phase,
                "step": step,
                "player": active,
            }
            waiting_triggers = collect_trigger_items(
                self,
                "step.begin",
                context,
            )
            if step == "end_step" and self.state.monarch == active:
                monarch = str(self.state.monarch)
                waiting_triggers.append(
                    self._monarch_trigger(
                        controller=monarch,
                        label="The monarch — draw a card",
                        effects=(
                            {
                                "op": "draw",
                                "player": monarch,
                                "count": 1,
                                "private": True,
                                "reason": "the monarch's end-step trigger",
                            },
                        ),
                        context={
                            "event": "step.begin",
                            "phase": phase,
                            "step": step,
                            "player": active,
                            "monarch_at_trigger": monarch,
                            "inherent_rule": "CR 725.2a",
                        },
                    )
                )
            enqueue_trigger_batch(self, waiting_triggers)
            self._grant_priority(active)
            return

        if step == "upkeep":
            # All abilities that triggered since the last priority window,
            # including during untap, form one APNAP/controller-order batch.
            # A delayed trigger must not suppress permanent-based upkeep
            # triggers, and trigger time within the no-priority interval must
            # not determine stack order.
            context = control_history.upkeep_trigger_context(
                self.state, phase, step, active
            )
            waiting_triggers = collect_trigger_items(
                self,
                "step.begin",
                context,
                held_triggers=held_triggers,
            )
            enqueue_trigger_batch(self, waiting_triggers)
            self._grant_priority(active)
            return

        if step == "draw":
            first_turn = self.state.turn_sequence == 1
            should_draw = not first_turn or self.state.config.effective_first_player_draws(len(self.seats))
            if self.state.config.auto_draw and should_draw:
                self._begin_draw_sequence(
                    active,
                    1,
                    reason="turn-based draw",
                    continuation={
                        "kind": "turn_draw",
                        "seat": active,
                    },
                )
                return
            elif not should_draw:
                self._log(active, "draw.skip", f"{active} skipped the first-turn draw.", importance=0)
            self._complete_draw_step_entry(active)
            return

        if not turn_counter_coordination.complete_ordinary_priority_step_entry(
            self,
            waiting_at_priority,
            grant_priority=False,
        ):
            return
        if step == "declare_attackers":
            self._issue_attackers()
            return
        if step == "declare_blockers":
            self._begin_blocker_decisions()
            return
        if step == "combat_damage":
            self._begin_combat_damage()
            return
        self._grant_priority(active)

    def _advance_step(
        self,
        *,
        held_triggers: Sequence[StackItem] = (),
    ) -> None:
        self.turn_steps.advance_step(held_triggers=held_triggers)

    def _finish_combat_phase(self) -> None:
        """Remove every represented object from combat at the CR 511.3 boundary."""

        changed_objects: list[str] = []
        for card in sorted(
            self.state.cards.values(),
            key=lambda candidate: (candidate.ref, candidate.object_id),
        ):
            if card.attacking is None and card.blocking is None:
                continue
            card.attacking = None
            card.blocking = None
            changed_objects.append(card.object_id)
        previous = self.state.combat
        self.state.combat = CombatState()
        self._log(
            None,
            "combat.end",
            "The combat phase ended and all objects were removed from combat.",
            {
                "attackers": len(previous.attackers),
                "blockers": sum(
                    len(blockers)
                    for blockers in previous.blockers.values()
                ),
                "defending_players": list(previous.defending_players),
            },
            importance=0,
            changed_objects=changed_objects,
        )

    def _remove_object_from_combat(
        self,
        card: CardInstance,
        *,
        reason: str,
    ) -> bool:
        """Clear one object's represented CR 506.4 combat relationships."""

        removal = remove_combat_relationships(
            self.state.combat,
            card.object_id,
        )
        was_attacker = card.attacking is not None or removal.was_attacker
        was_blocker = card.blocking is not None
        if not (
            was_attacker or was_blocker or removal.removed_as_blocker
        ):
            return False

        card.attacking = None
        card.blocking = None
        self._log(
            card.controller,
            "combat.remove",
            f"{card.ref} was removed from combat.",
            {
                "object": card.ref,
                "was_attacking": was_attacker,
                "was_blocking": was_blocker or removal.removed_as_blocker,
                "reason": reason,
            },
            importance=1,
            changed_objects=[card.object_id],
            changed_players=[card.controller],
        )
        return True

    def _remove_invalid_combat_objects(self) -> bool:
        """Remove represented combatants invalidated by CR 506.4 state."""

        candidates: list[CardInstance] = []
        candidate_ids: set[str] = set()
        for object_id in self.state.combat.attackers:
            card = self.state.cards.get(object_id)
            if card is not None and object_id not in candidate_ids:
                candidates.append(card)
                candidate_ids.add(object_id)
        for blocker_ids in self.state.combat.blockers.values():
            for object_id in blocker_ids:
                card = self.state.cards.get(object_id)
                if card is not None and object_id not in candidate_ids:
                    candidates.append(card)
                    candidate_ids.add(object_id)

        changed = False
        for card in candidates:
            data = self._effective_card_data(card)
            card_types, _, _ = self._type_parts(
                str(data.get("type_line") or "")
            )
            invalid_reason: str | None = None
            if card.zone != "battlefield":
                invalid_reason = "left the battlefield"
            elif card.phased_out:
                invalid_reason = "phased out"
            elif "creature" not in card_types:
                invalid_reason = "stopped being a creature"
            elif "battle" in card_types:
                invalid_reason = "became a Battle"
            elif (
                card.object_id in self.state.combat.attackers
                and card.controller != self.state.active_player
            ):
                invalid_reason = "attacker control changed"
            if invalid_reason is not None:
                changed = (
                    self._remove_object_from_combat(
                        card,
                        reason=invalid_reason,
                    )
                    or changed
                )
        return changed

    def _active_cleanup_frame(self) -> dict[str, Any] | None:
        return self.turn_steps.active_cleanup_frame()

    def _remove_cleanup_frames(self) -> None:
        self.turn_steps.remove_cleanup_frames()

    def _finish_cleanup(self) -> None:
        self.turn_steps.finish_cleanup()

    def _end_turn_now(self, *, actor: str, reason: str) -> None:
        self.turn_steps.end_turn_now(actor=actor, reason=reason)

    def _grant_priority(self, seat: str | None) -> None:
        self.turn_priority.grant_priority(seat)

    def _issue_priority(
        self, seat: str, hints: Mapping[str, Any] | None = None
    ) -> Any:
        return self.turn_priority.issue_priority(seat, hints)

    def _complete_priority(self, decision: Any) -> None:
        self.turn_priority.complete_priority(decision)

    def _set_yield(self, seat: str, value: Any) -> None:
        self.turn_priority.set_yield(seat, value)

    @staticmethod
    def _signature_hash(value: Any) -> str:
        return TurnPriorityDecisionOwner.signature_hash(value)

    def _stack_signature(self) -> str:
        return self.turn_priority.stack_signature()

    def meaningful_action_signature(
        self,
        seat: str,
        hints: Mapping[str, Any] | None = None,
    ) -> str:
        """Hash the currently executable strategic choices visible to ``seat``.

        Ordinary tap-for-mana actions are deliberately absent. They are payment
        mechanics for the cast/activation choices that do appear here and must
        not turn every empty priority pass into an LLM task.
        """

        return self.turn_priority.meaningful_action_signature(seat, hints)

    def _optimization_stats(self, seat: str) -> dict[str, Any]:
        return self.turn_priority.optimization_stats(seat)

    def _increment_optimization(self, seat: str, key: str) -> None:
        self.turn_priority.increment_optimization(seat, key)

    def _yield_stop_reason(
        self, seat: str, action_signature: str | None = None
    ) -> str | None:
        return self.turn_priority.yield_stop_reason(seat, action_signature)

    def _yield_stopped(self, seat: str) -> bool:
        return self._yield_stop_reason(seat) is not None

    def _can_auto_pass(
        self,
        seat: str,
        *,
        action_signature: str,
        meaningful: bool,
    ) -> tuple[bool, str | None]:
        return self.turn_priority.can_auto_pass(
            seat,
            action_signature=action_signature,
            meaningful=meaningful,
        )

    def _signature_has_actions(
        self, seat: str, hints: Mapping[str, Any] | None = None
    ) -> bool:
        return self.turn_priority.signature_has_actions(seat, hints)

    def _record_action_opportunity(
        self,
        seat: str,
        *,
        hints: Mapping[str, Any],
        action_signature: str,
        outcome: str,
        yield_invalidation: str | None = None,
    ) -> dict[str, Any]:
        return self.turn_priority.record_action_opportunity(
            seat,
            hints=hints,
            action_signature=action_signature,
            outcome=outcome,
            yield_invalidation=yield_invalidation,
        )

    def _pass_priority(self, seat: str, *, automatic: bool = False) -> None:
        self.turn_priority.pass_priority(seat, automatic=automatic)

    def pump(self, *, max_transitions: int = 1000) -> None:
        """Run deterministic system transitions until an external decision is needed."""
        self.turn_priority.pump(max_transitions=max_transitions)

    def _manual_active_main_phase_window(self, seat: str) -> bool:
        """Keep browser play under the active player's explicit control.

        Simulation providers can retain empty-window auto-passing. Interactive
        games opt in so an empty stack never carries the active player through
        either main phase without an explicit pass.
        """

        return self.turn_priority.manual_active_main_phase_window(seat)

    def _semantic_pause_annotation(self) -> dict[str, Any] | None:
        return next(
            (
                annotation
                for annotation in reversed(self.state.annotations)
                if annotation.get("kind") == "semantic_unsupported"
                and annotation.get("active", True)
            ),
            None,
        )

    def _pause_for_unsupported_semantic(
        self,
        *,
        item: StackItem | None = None,
        program: SemanticProgram | None = None,
        event: str | None = None,
        source: CardInstance | None = None,
    ) -> None:
        if self._semantic_pause_annotation() is not None:
            return
        label = (
            item.label
            if item is not None
            else source.printed_name
            if source is not None
            else "unsupported material semantic"
        )
        semantic_key = (
            item.semantic_key
            if item is not None
            else program.key
            if program is not None
            else None
        )
        trust_level = (
            program.trust_level if program is not None else "unresolved"
        )
        if (
            program is not None
            and program.trust_level == "trusted"
            and not self.semantic_program_is_current_trusted(program)
        ):
            trust_level = "source_hash_drift"
        annotation = {
            "kind": "semantic_unsupported",
            "active": True,
            "label": label,
            "semantic_key": semantic_key,
            "trust_level": trust_level,
            "stack": item.ref if item is not None else None,
            "event": event,
            "turn_sequence": self.state.turn_sequence,
            "phase": self.state.phase,
            "step": self.state.step,
            "semantic_policy": self.state.config.semantic_policy,
        }
        self.state.annotations.append(annotation)
        self.state.priority_player = None
        self._log(
            None,
            "fidelity.semantic_unsupported",
            (
                f"Paused before resolving material behavior for {label} "
                "under trusted-only semantic policy."
            ),
            annotation,
            importance=3,
        )

    # ------------------------------------------------------------------
    # Mana, land plays, spells, and abilities
    # ------------------------------------------------------------------
    def _commander_identity(self, seat: str) -> set[str]:
        colors: set[str] = set()
        for oracle_id in self.state.commander_oracle_ids[seat]:
            colors.update(self.card_db.by_oracle_id(oracle_id).color_identity)
        return colors

    @staticmethod
    def _mana_restriction_allows(
        restriction: str,
        spend_context: str | None,
    ) -> bool:
        is_spell = bool(spend_context and "spell" in spend_context)
        is_artifact = bool(
            spend_context and spend_context.startswith("artifact")
        )
        is_legendary = bool(
            spend_context and "legendary" in spend_context
        )
        if restriction == "artifact_spell_or_ability":
            return (
                (is_spell and is_artifact)
                or spend_context == "artifact_ability"
            )
        if restriction == "nonartifact_spell_prohibited":
            return not (is_spell and not is_artifact)
        if restriction == "legendary_spell_uncounterable":
            return is_spell and is_legendary
        return False

    def _spell_mana_spend_context(self, type_line: str) -> str:
        types, _, supertypes = self._type_parts(type_line)
        artifact = "artifact" in types
        legendary = "legendary" in supertypes
        if artifact and legendary:
            return "artifact_legendary_spell"
        if artifact:
            return "artifact_spell"
        if legendary:
            return "legendary_spell"
        return "nonartifact_spell"

    def _restricted_mana(self, seat: str) -> dict[str, dict[str, int]]:
        raw = self.state.players[seat].stats.setdefault(
            "restricted_mana",
            {},
        )
        return {
            str(key): normalize_mana_bundle(value)
            for key, value in dict(raw).items()
        }

    def _store_restricted_mana(
        self,
        seat: str,
        values: Mapping[str, Mapping[str, int]],
    ) -> None:
        compact = {
            key: {
                color: amount
                for color, amount in normalize_mana_bundle(bundle).items()
                if amount
            }
            for key, bundle in values.items()
            if sum(normalize_mana_bundle(bundle).values())
        }
        if compact:
            self.state.players[seat].stats["restricted_mana"] = compact
        else:
            self.state.players[seat].stats.pop("restricted_mana", None)

    def _add_restricted_mana(
        self,
        seat: str,
        restriction: str,
        bundle: Mapping[str, int],
    ) -> None:
        values = self._restricted_mana(seat)
        current = values.setdefault(
            restriction,
            normalize_mana_bundle(None),
        )
        for color, amount in normalize_mana_bundle(bundle).items():
            current[color] += amount
        self._store_restricted_mana(seat, values)

    def _spendable_mana_pool(
        self,
        seat: str,
        spend_context: str | None,
    ) -> dict[str, int]:
        pool = normalize_mana_bundle(self.state.players[seat].mana_pool)
        for restriction, bundle in self._restricted_mana(seat).items():
            if self._mana_restriction_allows(restriction, spend_context):
                continue
            for color, amount in bundle.items():
                pool[color] = max(0, pool[color] - amount)
        return pool

    def _apply_mana_spend(
        self,
        seat: str,
        spent: Mapping[str, int],
        spend_context: str | None,
    ) -> None:
        pool = normalize_mana_bundle(self.state.players[seat].mana_pool)
        restricted = self._restricted_mana(seat)
        for color, raw_amount in normalize_mana_bundle(spent).items():
            remaining = raw_amount
            for restriction in sorted(restricted):
                if not self._mana_restriction_allows(
                    restriction,
                    spend_context,
                ):
                    continue
                restricted_amount = restricted[restriction][color]
                use = min(remaining, restricted_amount)
                restricted[restriction][color] -= use
                if (
                    use
                    and restriction
                    == "legendary_spell_uncounterable"
                ):
                    self.state.players[seat].stats[
                        "next_spell_uncounterable"
                    ] = True
                remaining -= use
                if not remaining:
                    break
            pool[color] -= raw_amount
            if pool[color] < 0:
                raise GameRuleError(
                    "Mana payment exceeded the authoritative pool"
                )
        self.state.players[seat].mana_pool = pool
        self._store_restricted_mana(seat, restricted)

    def available_mana_sources(
        self,
        seat: str,
        *,
        spend_context: str | None = None,
    ) -> list[ManaSource]:
        return available_mana_sources(
            self,
            seat,
            spend_context=spend_context,
        )

    def _activate_mana_plan(
        self,
        seat: str,
        activations: Sequence[Mapping[str, Any]],
        *,
        spend_context: str | None = None,
        payment_id: str | None = None,
        replacement_selections_by_event: Mapping[str, Any] | None = None,
    ) -> None:
        complete_mana_plan_activations(
            self,
            seat,
            activations,
            spend_context=spend_context,
            payment_id=payment_id,
            replacement_selections_by_event=replacement_selections_by_event,
        )

    def _pay_for_cost(
        self,
        seat: str,
        requirements: dict[str, int],
        response: Mapping[str, Any],
        *,
        exclude_sources: set[str] | None = None,
        spend_context: str | None = None,
    ) -> tuple[dict[str, int], list[dict[str, Any]]]:
        activations: list[dict[str, Any]] = []
        pay_mode = response.get("pay", "auto")
        if pay_mode == "auto":
            plan = auto_plan_payment(
                requirements,
                [
                    source
                    for source in self.available_mana_sources(
                        seat,
                        spend_context=spend_context,
                    )
                    if source.object_id not in (exclude_sources or set())
                ],
                allow_conditional=(
                    bool(response.get("allow_conditional_mana", False))
                    and not self.state.config.strict_mana
                ),
                reserve=normalize_mana_bundle(response.get("reserve")),
                starting_pool=self._spendable_mana_pool(
                    seat,
                    spend_context,
                ),
            )
            activations = plan.activations
            self._activate_mana_plan(
                seat,
                activations,
                spend_context=spend_context,
                payment_id=str(response.get("_mana_payment_id") or "") or None,
                replacement_selections_by_event=(
                    response.get("_mana_replacement_selections")
                    if isinstance(
                        response.get("_mana_replacement_selections"), Mapping
                    )
                    else None
                ),
            )
            payment = plan.payment
        else:
            activations = [dict(item) for item in response.get("mana") or []]
            self._activate_mana_plan(
                seat,
                activations,
                spend_context=spend_context,
                payment_id=str(response.get("_mana_payment_id") or "") or None,
                replacement_selections_by_event=(
                    response.get("_mana_replacement_selections")
                    if isinstance(
                        response.get("_mana_replacement_selections"), Mapping
                    )
                    else None
                ),
            )
            payment = normalize_mana_bundle(response.get("payment"))
        try:
            _, spent = pay_mana_from_pool(
                self._spendable_mana_pool(seat, spend_context),
                requirements,
                payment=payment,
            )
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        self._apply_mana_spend(
            seat,
            spent,
            spend_context,
        )
        return spent, activations

    def _check_priority(self, seat: str) -> None:
        if self.state.priority_player != seat:
            raise GameRuleError(f"{seat} does not have priority")

    def _is_main_phase(self) -> bool:
        """Return whether the scheduler is at either CR 505 main phase."""

        return (self.state.phase, self.state.step) in {
            ("precombat_main", "main"),
            ("postcombat_main", "main"),
        }

    def _sorcery_timing(self, seat: str) -> None:
        if seat != self.state.active_player:
            raise GameRuleError("Sorcery-speed action requires the active player")
        if not self._is_main_phase():
            raise GameRuleError("Sorcery-speed action requires a main phase")
        if self.state.stack:
            raise GameRuleError("Sorcery-speed action requires an empty stack")

    def _temporary_play_permission(
        self,
        seat: str,
        card: CardInstance,
    ) -> Mapping[str, Any] | None:
        permission = card.annotations.get("temporary_play_permission")
        if not isinstance(permission, Mapping):
            return None
        if not temporary_play_permission_is_current(
            self.state,
            seat,
            card,
            permission,
        ):
            return None
        return permission

    def _compiled_land_play_permission(
        self,
        seat: str,
        card: CardInstance,
    ) -> bool:
        permission = self._temporary_play_permission(seat, card)
        if permission is not None and bool(
            permission.get("allow_land", True)
        ):
            return True
        if card.owner != seat:
            return False
        if card.zone == "hand":
            return True
        return bool(
            card.zone == "graveyard"
            and controller_has_action_permission(
                self,
                seat,
                ActionPermissionKind.LAND_PLAY_FROM_OWN_GRAVEYARD,
            )
        )

    @staticmethod
    def _land_play_faces(record: CardRecord) -> list[dict[str, Any] | None]:
        """Return the faces a player may choose for an ordinary land play."""

        if record.layout == "modal_dfc" and record.faces:
            return [
                dict(face)
                for face in record.faces
                if "land" in str(face.get("type_line") or "").casefold()
            ]
        if record.faces:
            front = dict(record.faces[0])
            if "land" in str(front.get("type_line") or "").casefold():
                return [front]
            return []
        return [None] if record.is_land else []

    def _land_entry_life_amount(
        self,
        record: CardRecord,
        face: Mapping[str, Any] | None = None,
    ) -> int:
        try:
            return typed_entry_life_payment_amount(
                self,
                record,
                prospective_name=(
                    str(face.get("name") or "") if face is not None else None
                ),
            )
        except SemanticNodeError as exc:
            raise GameRuleError(str(exc)) from exc

    def _play_land(self, seat: str, response: Mapping[str, Any]) -> None:
        self._check_priority(seat)
        self._sorcery_timing(seat)
        player = self.state.players[seat]
        if player.land_plays_remaining <= 0:
            raise GameRuleError("No land plays remain")
        raw_from = str(response.get("from") or "hand")
        card = self._resolve_object(
            seat,
            str(response.get("card") or response.get("id")),
            zones={raw_from},
            owned_only=False,
        )
        record = self.card_record(card)
        if not record:
            raise GameRuleError(f"{card.printed_name} is not a land")
        requested_face = str(response.get("face") or "")
        legal_faces = self._land_play_faces(record)
        face = next(
            (
                candidate
                for candidate in legal_faces
                if candidate is not None
                and str(candidate.get("name") or "").casefold()
                == requested_face.casefold()
            ),
            None,
        )
        if requested_face and face is None:
            raise GameRuleError(
                f"{requested_face!r} is not a playable land face of {record.name}"
            )
        if not requested_face:
            if len(legal_faces) != 1:
                raise GameRuleError("Choose which land face to play")
            face = legal_faces[0]
        if not legal_faces:
            raise GameRuleError(f"{card.printed_name} is not a land")
        if not self._compiled_land_play_permission(seat, card):
            raise GameRuleError(
                f"Playing {card.printed_name} from {card.zone} is not "
                "authorized by a compiled zone permission."
            )
        if "enters_tapped" in response or "tapped" in response:
            raise GameRuleError("Land entry state is derived by the rules engine")
        raw_entry_selections = response.get(
            "_entry_replacement_selections", ()
        )
        if not isinstance(raw_entry_selections, (list, tuple)):
            raise GameRuleError("Land-entry replacement journal is malformed")
        pay_entry_life = bool(response.get("pay_life", False))
        entry_life_amount = self._land_entry_life_amount(record, face)
        if pay_entry_life:
            if entry_life_amount <= 0:
                raise GameRuleError(
                    "This land play does not authorize an entry life payment"
                )
        life_before = player.life
        card.annotations.pop("temporary_play_permission", None)
        self.move_card(
            card.object_id,
            "battlefield",
            controller=seat,
            entry_pay_life=pay_entry_life,
            enter_face=(str(face.get("name")) if face is not None else None),
            reason="land play",
            log=False,
            semantic_events=True,
            replacement_selections=tuple(raw_entry_selections),
        )
        tapped = card.tapped
        life_paid = life_before - player.life
        player.land_plays_remaining -= 1
        self._log(
            seat,
            "land.play",
            f"{seat} played {card.ref} "
            f"{str(face.get('name')) if face is not None else card.printed_name}"
            f"{' tapped' if tapped else ''}.",
            {
                "object": card.ref,
                "tapped": tapped,
                "life_paid": life_paid,
                "face": str(face.get("name")) if face is not None else None,
            },
            importance=2,
            changed_objects=[card.object_id],
            changed_players=[seat],
        )
        # CR 117.5 and 704.3 require state-based actions and waiting triggers
        # to be handled before the active player receives priority after this
        # special action.  Without this boundary, an enters trigger created by
        # a land play could remain queued while the player cast another spell
        # or even advanced to the next step.
        self.state.priority_player = None
        self.state.priority_passes = []
        if self._stabilize():
            return
        self.state.priority_player = seat

    def _select_cast_face(self, record: CardRecord, face_name: str | None) -> dict[str, Any] | None:
        if not record.faces:
            return None
        if face_name:
            for face in record.faces:
                if str(face.get("name") or "").casefold() == face_name.casefold():
                    return dict(face)
            raise GameRuleError(f"{face_name!r} is not a face of {record.name}")
        return dict(record.faces[0])

    @staticmethod
    def _front_face(record: CardRecord) -> dict[str, Any] | None:
        return dict(record.faces[0]) if record.faces else None

    @staticmethod
    def _trusted_generic_spell(record: CardRecord) -> bool:
        """Whether the spell's ordinary permanent resolution is core rules.

        A permanent's static, triggered, and activated abilities do not make
        casting the permanent itself illegal.  Those abilities are evaluated
        independently when their event occurs; strict semantic policy can
        still pause before an unsupported ability mutates state.  Blocking the
        cast based on unrelated Oracle text made ordinary creatures and
        commanders disappear from legal actions.
        """

        return record.is_permanent_spell

    def _compiled_zone_cast_permission(
        self,
        seat: str,
        card: CardInstance,
    ) -> bool:
        """Return whether a trusted static permission allows this zone cast."""

        if compiled_ordinary_zone_cast_permission(self, seat, card):
            return True
        return bool(
            card.owner == seat
            and card.zone == "graveyard"
            and compiled_fixed_mana_flashback_spec(self, card) is not None
        )

    def _cast(
        self,
        seat: str,
        response: Mapping[str, Any],
        *,
        authorized_from_zone: str | None = None,
        required_face: str | None = None,
        force_without_mana_cost: bool = False,
        ignore_priority: bool = False,
        ignore_timing: bool = False,
        during_resolution: bool = False,
    ) -> None:
        request = CastProposalRequest.from_submission(
            seat,
            response,
            authorized_from_zone=authorized_from_zone,
            required_face=required_face,
            force_without_mana_cost=force_without_mana_cost,
            ignore_priority=ignore_priority,
            ignore_timing=ignore_timing,
            during_resolution=during_resolution,
        )
        try:
            proposal = build_cast_proposal(self, request)
            commit_cast(self, proposal, response)
        except CastProposalError as exc:
            raise GameRuleError(str(exc)) from exc

    def _activated_abilities(self, card: CardInstance) -> tuple[ActivatedAbility, ...]:
        return activated_abilities(self, card)

    @staticmethod
    def _semantic_key_for_ability(
        source: CardInstance,
        ability: ActivatedAbility,
    ) -> str:
        if ability.builtin_semantic_key is not None:
            return ability.builtin_semantic_key
        return f"{source.oracle_id}:ability:{ability.ability_id}"

    def _legendary_creatures_controlled(self, seat: str) -> int:
        total = 0
        for object_id in self.state.players[seat].zones["battlefield"]:
            card = self.state.cards[object_id]
            if card.controller != seat or card.phased_out:
                continue
            type_line = str(self._effective_card_data(card).get("type_line") or "").casefold()
            if "legendary" in type_line and "creature" in type_line:
                total += 1
        return total

    def _pay_ability_choice_costs(
        self,
        seat: str,
        source: CardInstance,
        ability: ActivatedAbility,
        response: Mapping[str, Any],
    ) -> list[str]:
        values = list(response.get("cost_cards") or response.get("cost_objects") or [])
        required = sum(choice.count for choice in ability.choices)
        if len(values) != required:
            if required:
                raise GameRuleError(f"Ability requires exactly {required} selected cost card(s)")
            if values:
                raise GameRuleError("This ability has no selectable card cost")
        used: list[str] = []
        cursor = 0
        for choice in ability.choices:
            for _ in range(choice.count):
                value = str(values[cursor])
                cursor += 1
                if value not in activation_choice_candidates(
                    self, seat, source, choice
                ):
                    raise GameRuleError(
                        f"{value} is not eligible to pay this activation cost"
                    )
                if choice.zone == "battlefield":
                    card = self._resolve_object(
                        seat,
                        value,
                        zones={"battlefield"},
                        controlled_only=True,
                    )
                else:
                    card = self._resolve_object(
                        seat,
                        value,
                        zones={choice.zone},
                        owned_only=True,
                    )
                if card.object_id in used:
                    raise GameRuleError("The same object cannot pay the same activation cost twice")
                if choice.another and card.object_id == source.object_id:
                    raise GameRuleError("An 'another' cost cannot use the ability source")
                if choice.card_type:
                    type_line = str(self._effective_card_data(card).get("type_line") or "").casefold()
                    if choice.card_type not in type_line:
                        raise GameRuleError(f"{card.ref} is not a {choice.card_type}")
                used.append(card.object_id)
                typed_cost = choice.fixed_zone_change_cost()
                destination = (
                    typed_cost.destination_zone
                    if typed_cost is not None
                    else {
                        "return": "hand",
                        "exile": "exile",
                    }.get(choice.kind, "graveyard")
                )
                raw_journal = response.get(
                    "_mana_replacement_selections"
                ) or {}
                if not isinstance(raw_journal, Mapping):
                    raise GameRuleError(
                        "Activation replacement journal is malformed"
                    )
                zone_entries = tuple(
                    (event_id, selections)
                    for event_id, selections in raw_journal.items()
                    if type(event_id) is str
                    and event_id.startswith("zone.change:")
                    and event_id.endswith(f":{card.ref}")
                )
                if len(zone_entries) > 1:
                    raise GameRuleError(
                        "Activation replacement journal is ambiguous"
                    )
                selections = zone_entries[0][1] if zone_entries else ()
                if not isinstance(selections, (list, tuple)):
                    raise GameRuleError(
                        "Activation replacement selections are malformed"
                    )
                self.move_card(
                    card.object_id,
                    destination,
                    reason="activated ability cost",
                    semantic_events=True,
                    replacement_selections=tuple(selections),
                )
        return used

    def _mana_output_for_ability(
        self,
        seat: str,
        source: CardInstance,
        ability: ActivatedAbility,
        response: Mapping[str, Any],
    ) -> dict[str, int]:
        return mana_output_for_ability(
            self, seat, source, ability, response
        )

    def _mana_modes_for_ability(
        self,
        seat: str,
        source: CardInstance,
        ability: ActivatedAbility,
    ) -> tuple[ManaMode, ...]:
        return mana_modes_for_ability(self, seat, source, ability)

    def _recordless_mana_modes(
        self,
        seat: str,
        source: CardInstance,
    ) -> list[ManaMode]:
        """Return safely executable modes for a rules-created token.

        A token can have complete characteristics without a Scryfall-backed
        CardRecord.  Only a represented tap-mana ability whose remaining costs
        are fully compiled is eligible for automatic payment.
        """

        compiled_modes: list[ManaMode] = []
        for ability in self._activated_abilities(source):
            if (
                not ability.mana_ability
                or source.zone not in ability.zones
                or not ability.tap_source
                or not ability.compiled_cost
                or sum(ability.mana.values())
                or ability.choices
                or ability.untap_source
                or ability.discard_source
                or ability.exile_source
                or ability.life_payment
                or ability.energy_payment
                or ability.loyalty_delta is not None
                or activation_condition_status(
                    self, seat, ability, source
                )[0]
                != "payable"
            ):
                continue
            for mode in self._mana_modes_for_ability(
                seat, source, ability
            ):
                compiled_modes.append(
                    ManaMode(
                        mode.bundle,
                        conditional=mode.conditional,
                        restriction=mode.restriction,
                        side_effects=mode.side_effects,
                        requires_choice=mode.requires_choice,
                    )
                )
        return compiled_modes

    def _activate(self, seat: str, response: Mapping[str, Any]) -> None:
        request = ActivationProposalRequest.from_submission(seat, response)
        try:
            proposal = build_activation_proposal(self, request)
            commit_activation(self, proposal, response)
        except ActivationProposalError as exc:
            raise GameRuleError(str(exc)) from exc

    def _ability_choice_payable(
        self,
        seat: str,
        source: CardInstance,
        ability: ActivatedAbility,
    ) -> bool:
        slots: list[list[str]] = []
        for choice in ability.choices:
            candidates = list(
                activation_choice_candidates(self, seat, source, choice)
            )
            for _ in range(choice.count):
                slots.append(candidates)

        def assign(index: int, used: set[str]) -> bool:
            if index >= len(slots):
                return True
            for object_id in slots[index]:
                if object_id in used:
                    continue
                used.add(object_id)
                if assign(index + 1, used):
                    return True
                used.remove(object_id)
            return False

        return assign(0, set())

    @staticmethod
    def _crew_threshold(ability: ActivatedAbility) -> int | None:
        return ability.crew_threshold

    def _crew_candidates(
        self,
        seat: str,
        source: CardInstance,
    ) -> list[CrewCandidate]:
        try:
            return list(current_crew_candidates(self, seat, source))
        except CrewAbilityError as exc:
            raise GameRuleError(str(exc)) from exc

    def _pay_crew_cost(
        self,
        seat: str,
        source: CardInstance,
        ability: ActivatedAbility,
        response: Mapping[str, Any],
    ) -> tuple[list[str], FrozenMap]:
        threshold = self._crew_threshold(ability)
        if threshold is None:
            raise GameRuleError("Crew threshold is not compiled")
        try:
            return pay_crew_cost(
                self,
                seat=seat,
                source=source,
                threshold=threshold,
                response=response,
            )
        except CrewAbilityError as exc:
            raise GameRuleError(str(exc)) from exc

    def _ability_availability(
        self,
        seat: str,
        card: CardInstance,
        ability: ActivatedAbility,
    ) -> tuple[str, str | None]:
        return activation_availability(self, seat, card, ability)

    def _loyalty_cost_modifier_present(self) -> bool:
        """Fail closed when a public effect modifies loyalty costs.

        Loyalty abilities can belong to any permanent, not only a
        planeswalker (CR 606.2-3).  The base loyalty-symbol cost is compiled,
        but the generic cost-modification ordering needed by CR 606.4-5 is not
        yet represented.  A visible modifier therefore makes the activation
        unresolved instead of executable at an incorrect cost.
        """

        return bool(active_loyalty_cost_modifiers(self))

    def _may_activate_creature_as_haste(
        self,
        seat: str,
        card: CardInstance,
    ) -> bool:
        types, _, _ = self._type_parts(
            str(self._effective_card_data(card).get("type_line") or "")
        )
        return bool(
            "creature" in types
            and controller_has_action_permission(
                self,
                seat,
                ActionPermissionKind.ACTIVATE_CONTROLLED_CREATURE_AS_HASTE,
            )
        )

    def _activation_condition_status(
        self,
        seat: str,
        ability: ActivatedAbility,
        source: CardInstance | None = None,
    ) -> tuple[str, str | None]:
        """Compatibility port for the extracted read-only condition owner."""

        return activation_condition_status(self, seat, ability, source)

    def _cost_is_affordable(
        self,
        seat: str,
        requirements: Mapping[str, int],
        *,
        exclude_sources: set[str] | None = None,
        spend_context: str | None = None,
    ) -> bool:
        remaining = {key: int(requirements.get(key, 0)) for key in ("GENERIC", "W", "U", "B", "R", "G", "C")}
        pool = self._spendable_mana_pool(seat, spend_context)
        for color in "WUBRGC":
            paid = min(pool[color], remaining[color])
            pool[color] -= paid
            remaining[color] -= paid
        generic_paid = min(sum(pool.values()), remaining["GENERIC"])
        remaining["GENERIC"] -= generic_paid
        if not sum(remaining.values()):
            return True
        try:
            sources = [
                source
                for source in self.available_mana_sources(
                    seat,
                    spend_context=spend_context,
                )
                if source.object_id not in (exclude_sources or set())
            ]
            auto_plan_payment(remaining, sources)
            return True
        except ManaPlanError:
            return False


    @staticmethod
    def _mana_vector(value: Mapping[str, Any] | None) -> dict[str, int]:
        return {
            key: int((value or {}).get(key, 0))
            for key in ("GENERIC", "W", "U", "B", "R", "G", "C")
        }

    def _controls_commander(self, seat: str) -> bool:
        return any(
            self.state.cards[object_id].controller == seat
            and self.state.cards[object_id].is_commander
            for object_id in self.state.players[seat].zones["battlefield"]
        )

    def _alternate_cost_condition_met(
        self,
        seat: str,
        condition: Mapping[str, Any],
    ) -> bool:
        if condition.get("not_your_turn") and self.state.active_player == seat:
            return False
        if condition.get("your_turn") and self.state.active_player != seat:
            return False
        if condition.get("control_commander") and not self._controls_commander(
            seat
        ):
            return False
        return True

    def _exile_cost_candidates(
        self,
        seat: str,
        source: CardInstance,
        specification: Mapping[str, Any],
    ) -> list[str]:
        colors = {
            str(value).upper()
            for value in specification.get("colors_any", [])
        }
        candidates: list[str] = []
        for object_id in self.state.players[seat].zones["hand"]:
            card = self.state.cards[object_id]
            if (
                specification.get("exclude_source", True)
                and card.object_id == source.object_id
            ):
                continue
            record = self.card_record(card)
            if record is None:
                continue
            if colors and not colors.intersection(
                {str(value).upper() for value in record.colors}
            ):
                continue
            candidates.append(card.ref)
        return candidates

    def _cost_payment_mechanics(
        self,
        record: CardRecord,
        schema: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        declared = schema.get("payment_mechanics") or []
        mechanics = [
            dict(value) if isinstance(value, Mapping) else {"kind": str(value)}
            for value in declared
        ]
        return mechanics

    def _compiled_printed_cost(
        self,
        seat: str,
        card: CardInstance,
        *,
        x_value: int | None,
        hint: bool,
    ) -> tuple[dict[str, int] | None, bool]:
        record = self.card_record(card)
        if record is None:
            return None, False

    def _compiled_printed_cost_options(
        self,
        seat: str,
        card: CardInstance,
        *,
        x_value: int | None,
        hint: bool,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Expand an ordinary or hybrid printed cost into exact alternatives."""

        record = self.card_record(card)
        if record is None:
            return [], False
        face = self._front_face(record)
        mana_cost = (
            str(face.get("mana_cost") or "")
            if face is not None
            else record.mana_cost
        )
        variants = [self._mana_vector(None)]
        has_x = False
        for symbol in parse_mana_symbols(mana_cost):
            if symbol.isdigit():
                for variant in variants:
                    variant["GENERIC"] += int(symbol)
                continue
            if symbol in "WUBRGC" and len(symbol) == 1:
                for variant in variants:
                    variant[symbol] += 1
                continue
            if symbol == "X":
                has_x = True
                if x_value is None and not hint:
                    raise GameRuleError(
                        f"Casting {record.name} requires an explicit X value"
                    )
                selected_x = 0 if x_value is None else int(x_value)
                if selected_x < 0:
                    raise GameRuleError("X cannot be negative")
                for variant in variants:
                    variant["GENERIC"] += selected_x
                continue
            hybrid = symbol.split("/")
            if len(hybrid) == 2 and all(
                part in "WUBRGC" and len(part) == 1
                for part in hybrid
            ):
                expanded: list[dict[str, int]] = []
                for variant in variants:
                    for color in hybrid:
                        choice = self._mana_vector(variant)
                        choice[color] += 1
                        expanded.append(choice)
                variants = expanded
                continue
            two_hybrid = symbol.split("/")
            if (
                len(two_hybrid) == 2
                and "2" in two_hybrid
                and any(
                    part in "WUBRGC" and len(part) == 1
                    for part in two_hybrid
                )
            ):
                color = next(part for part in two_hybrid if part != "2")
                expanded = []
                for variant in variants:
                    generic_choice = self._mana_vector(variant)
                    generic_choice["GENERIC"] += 2
                    expanded.append(generic_choice)
                    color_choice = self._mana_vector(variant)
                    color_choice[color] += 1
                    expanded.append(color_choice)
                variants = expanded
                continue
            return [], has_x
        commander_tax = (
            2
            * self.state.players[seat].commander_casts.get(card.oracle_id, 0)
            if card.zone == "command" and card.is_commander
            else 0
        )
        unique: list[dict[str, int]] = []
        seen: set[tuple[int, ...]] = set()
        for variant in variants:
            variant["GENERIC"] += commander_tax
            identity = tuple(
                variant[key]
                for key in ("GENERIC", "W", "U", "B", "R", "G", "C")
            )
            if identity not in seen:
                seen.add(identity)
                unique.append(variant)
        return [
            {
                "id": "normal" if len(unique) == 1 else f"hybrid-{index}",
                "kind": "mana" if len(unique) == 1 else "hybrid",
                "requirements": variant,
            }
            for index, variant in enumerate(unique, start=1)
        ], has_x
        commander_tax = (
            2
            * self.state.players[seat].commander_casts.get(card.oracle_id, 0)
            if card.zone == "command" and card.is_commander
            else 0
        )
        try:
            return parsed_cost(record.mana_cost, commander_tax), False
        except ManaPlanError:
            fixed, complex_symbols = mana_cost_to_vector(record.mana_cost)
            if complex_symbols and set(complex_symbols) == {"X"}:
                if x_value is None and not hint:
                    raise GameRuleError(
                        f"Casting {record.name} requires an explicit X value"
                    )
                selected_x = 0 if x_value is None else int(x_value)
                if selected_x < 0:
                    raise GameRuleError("X cannot be negative")
                fixed["GENERIC"] += (
                    selected_x * complex_symbols.count("X") + commander_tax
                )
                return self._mana_vector(fixed), True
            return None, False

    def _maximum_affordable_x(
        self,
        seat: str,
        card: CardInstance,
        *,
        limit: int = 100,
    ) -> int:
        spend_context = self._spell_mana_spend_context(
            str(
                self._effective_card_data(card).get("type_line")
                or ""
            )
        )
        maximum = -1
        for value in range(limit + 1):
            options, _ = self._compiled_printed_cost_options(
                seat,
                card,
                x_value=value,
                hint=False,
            )
            if not any(
                self._cost_is_affordable(
                    seat,
                    option["requirements"],
                    spend_context=spend_context,
                )
                for option in options
            ):
                break
            maximum = value
        return maximum

    def _priority_action_hints(self, seat: str) -> dict[str, Any]:
        return build_priority_action_catalog(self, seat)

    def _priority_window_empty(
        self, seat: str, hints: Mapping[str, Any] | None = None
    ) -> bool:
        """Whether the implemented action grammar exposes no priority action.

        Concede is deliberately ignored: the simulator should not spend an LLM
        call merely to offer concession at every priority window. The setting
        can be disabled for debugging or for a future client that implements
        additional special actions not yet represented by the kernel.
        """

        hints = dict(hints or self._priority_action_hints(seat))
        return not any(hints.get(key) for key in ("cast", "lands", "abilities", "special_actions"))

    # ------------------------------------------------------------------
    # Stack resolution and arbiter role
    # ------------------------------------------------------------------
    def _semantic_event_value(
        self,
        value: Any,
        *,
        source: CardInstance,
        context: Mapping[str, Any],
    ) -> Any:
        return semantic_event_value(
            self,
            value,
            source=source,
            context=context,
        )

    def _semantic_event_condition_matches(
        self,
        condition: Mapping[str, Any],
        *,
        source: CardInstance,
        context: Mapping[str, Any],
    ) -> bool:
        return semantic_event_condition_matches(
            self,
            condition,
            source=source,
            context=context,
        )

    def _semantic_event_matches(
        self,
        program: SemanticProgram,
        source: CardInstance,
        event: str,
        context: Mapping[str, Any],
        *,
        source_zone: str | None = None,
    ) -> bool:
        return semantic_event_matches(
            self,
            program,
            source,
            event,
            context,
            source_zone=source_zone,
        )

    def _dispatch_semantic_event(
        self,
        event: str,
        context: Mapping[str, Any],
        *,
        sources: Sequence[CardInstance] | None = None,
        source_zones: Mapping[str, str] | None = None,
        source_characteristics: Mapping[
            str, Mapping[str, Any]
        ] | None = None,
        trigger_batch: list[StackItem] | None = None,
    ) -> list[str]:
        return dispatch_semantic_event(
            self,
            event,
            context,
            sources=sources,
            source_zones=source_zones,
            source_characteristics=source_characteristics,
            trigger_batch=trigger_batch,
        )


    @staticmethod
    def _type_parts(type_line: str) -> tuple[set[str], set[str], set[str]]:
        return type_parts(type_line)

    def _program_can_auto_resolve(self, item: StackItem) -> bool:
        if is_builtin_activation_semantic(
            item.semantic_key
        ) or item.semantic_key in {
            "builtin:sacrifice-source",
            STORM_SEMANTIC_KEY,
        }:
            return True
        program = self.semantics.get(item.semantic_key)
        target_schema = self._stack_target_schema(item, program)
        if (
            program
            and target_schema
            and not item.targets
            and not item.context.get("targets_chosen_at_creation")
        ):
            public_schema = self._public_target_schema(
                item.controller,
                target_schema,
                source_ref=item.ref,
            )
            if public_schema is None:
                self._counter_stack_item(
                    item.ref,
                    reason="no legal targets",
                    as_rule=True,
                    countered_by=item.controller,
                )
                self._grant_priority(self.state.active_player)
                return
            self.permissions.issue(
                kind="semantic.target",
                role="pilot",
                actors=[item.controller],
                allowed_actions=["choose"],
                payload_by_actor={
                    item.controller: {
                        "stack": item.ref,
                        "prompt": f"Choose legal targets for {program.label}.",
                        "target_schema": public_schema,
                        "legal_actions": [
                            {
                                "id": "choose",
                                "action": "choose",
                                "target_schema": public_schema,
                            }
                        ],
                    }
                },
                continuation={
                    "selection": self._target_selection_continuation(
                        actor=item.controller,
                        item=item,
                        public_schema=public_schema,
                    ).to_dict()
                },
            )
            return
        if (
            program
            and program.trust_level in {"trusted", "provisional", "intentionally_ignored"}
            and not program.requires_arbiter
        ):
            return True
        if (
            item.kind == "spell_copy"
            and item.context.get("copy_permanent_spell")
        ):
            return True
        return False

    def _prepare_stack_resolution(self) -> None:
        if self.state.pending_trigger_batches and self._stabilize():
            return
        if not self.state.stack:
            self._advance_step()
            return
        item = self.state.stack[-1]
        if self._begin_battle_entry_protector_choice(item):
            return
        if self._begin_intrinsic_exile_cast_resolution(item):
            return
        if item.semantic_key == STORM_SEMANTIC_KEY:
            self._prepare_storm_resolution(item)
            return
        if item.context.get("builtin") == "fetch_land":
            if not item.context.get("choice_made"):
                self._begin_fetch_search(item)
                return
            self._resolve_fetch_land(item)
            return
        if item.semantic_key == "builtin:sacrifice-source":
            self._begin_resolve_item(
                item,
                [{"op": "sacrifice_if_present", "card": "$source"}],
                None,
                note="Mishra delayed sacrifice",
            )
            return
        builtin_activation = builtin_activation_resolution(
            item.semantic_key, item.controller
        )
        if builtin_activation is not None:
            self._begin_resolve_item(
                item,
                builtin_activation.effect_dicts(),
                None,
                note=builtin_activation.note,
            )
            return
        if item.semantic_key == "builtin:ward":
            self._begin_resolve_item(
                item,
                [
                    {
                        "op": "counter_unless_pay",
                        "player": str(item.context["payer"]),
                        "stack": str(item.context["target_stack"]),
                        "cost": dict(item.context.get("cost") or {}),
                    }
                ],
                None,
                note="Ward trigger resolved",
            )
            return
        if attack_transitions.prepare_attack_keyword_trigger_resolution(
            self, item
        ) or block_triggers.prepare_block_keyword_trigger_resolution(self, item):
            return
        if item.semantic_key == "builtin:daretti-emblem":
            card_ref = str(item.context.get("card") or "")
            card_zone_change_counter = item.context.get(
                "card_zone_change_counter"
            )
            self._begin_resolve_item(
                item,
                [
                    {
                        "op": "delayed_trigger",
                        "controller": item.controller,
                        "label": (
                            f"Return {card_ref} with Daretti's emblem"
                        ),
                        "event": "step.begin",
                        "condition": {
                            "phase": "ending",
                            "step": "end_step",
                        },
                        "stack": {
                            "label": (
                                f"Return {card_ref} with Daretti's emblem"
                            ),
                            "context": {
                                "dynamic_effects": [
                                    {
                                        "op": "move_if_in_zone",
                                        "card": card_ref,
                                        "from": "graveyard",
                                        "destination": "battlefield",
                                        "controller": item.controller,
                                        "expected_zone_change_counter": (
                                            card_zone_change_counter
                                        ),
                                    }
                                ]
                            },
                        },
                        "once": True,
                    }
                ],
                None,
                note="Daretti emblem delayed return scheduled",
            )
            return
        program = self.semantics.get(item.semantic_key)
        if (
            program is not None
            and "intervening_condition" in program.coverage
            and program.event_condition is not None
        ):
            source = self.state.cards.get(item.source_object_id or "")
            condition_holds = bool(
                source is not None
                and source.zone == program.active_zone
                and self._semantic_event_condition_matches(
                    program.event_condition,
                    source=source,
                    context=item.context,
                )
            )
            if not condition_holds:
                self.state.stack.remove(item)
                self._log(
                    item.controller,
                    "stack.trigger.removed",
                    (
                        f"Removed {item.ref}: {item.label}; its intervening "
                        "condition was false on resolution."
                    ),
                    {
                        "stack": item.ref,
                        "reason": "intervening_condition_false",
                    },
                    importance=2,
                )
                if not self._stabilize():
                    self._grant_priority(self.state.active_player)
                return
        trusted_generic_resolution = trusted_generic_empty_resolution(
            self, item, program
        )
        if (
            self.state.config.semantic_policy == "trusted_only"
            and (
                (
                    program is None
                    and not trusted_generic_resolution
                )
                or (
                    program is not None
                    and (
                        not self.semantic_program_is_current_trusted(
                            program
                        )
                        or program.requires_arbiter
                    )
                )
            )
            and item.context.get("dynamic_effects") is None
        ):
            self._pause_for_unsupported_semantic(
                item=item,
                program=program,
            )
            return
        target_schema = self._stack_target_schema(item, program)
        if (
            program
            and target_schema
            and not item.targets
            and not item.context.get("targets_chosen_at_creation")
        ):
            # Triggered semantics acquire controller-chosen targets when the
            # trigger is put onto/processed from the stack. Spell targets were
            # already validated at cast time.
            self._program_can_auto_resolve(item)
            return
        if (
            program
            and program.trust_level in {"trusted", "provisional", "intentionally_ignored"}
            and not program.requires_arbiter
        ):
            option_effects = item.context.get("cast_option_effects")
            self._begin_resolve_item(
                item,
                (
                    [dict(effect) for effect in option_effects]
                    if option_effects is not None
                    else [
                        *program.effects,
                        *(
                            _stack_mode_effects(target_schema, item)
                            if target_schema
                            else []
                        ),
                    ]
                ),
                program.destination or item.default_destination,
                note=program.notes,
            )
            return
        if item.context.get("dynamic_effects") is not None:
            self._begin_resolve_item(
                item,
                list(item.context.get("dynamic_effects") or []),
                item.default_destination,
                note=item.notes,
            )
            return
        if trusted_generic_resolution is not None:
            self._begin_resolve_item(
                item,
                [],
                trusted_generic_resolution.destination,
                note=trusted_generic_resolution.note,
            )
            return
        if self._program_can_auto_resolve(item):
            self._begin_resolve_item(
                item,
                [],
                item.default_destination,
                note=(
                    "Permanent spell resolved to the battlefield; no entry "
                    "trigger semantics applied"
                ),
            )
            return
        self.permissions.issue(
            kind="arbiter.resolve",
            role="arbiter",
            actors=["arbiter"],
            allowed_actions=["resolve", "register_and_resolve", "counter_as_rule", "fizzle"],
            payload_by_actor={
                "arbiter": {
                    "stack": item.ref,
                    "label": item.label,
                    "controller": item.controller,
                    "semantic_key": item.semantic_key,
                    "targets": item.targets,
                    "default_destination": item.default_destination,
                }
            },
        )

    def _create_copy_object(
        self,
        *,
        controller: str,
        source: CardInstance | None,
        characteristics: Mapping[str, Any],
        object_kind: str,
        zone: str,
    ) -> CardInstance:
        """Create one serialized noncard copy object.

        Stack copies are associated with a ``StackItem`` by their caller.
        Copies in ordinary zones use normal owner-zone membership until the
        next state-based-action check makes them cease.
        """

        self._require_seat(controller, in_game=True)
        if object_kind not in {"spell_copy", "card_copy"}:
            raise GameRuleError("A copy object needs a typed copy kind")
        if zone not in {
            "library",
            "hand",
            "battlefield",
            "graveyard",
            "exile",
            "command",
            "stack",
        }:
            raise GameRuleError(f"Unsupported copy-object zone {zone}")
        ref = self._next_ref("O")
        object_id = self._stable_runtime_id("copy-object", ref)
        copied_values = copy.deepcopy(dict(characteristics))
        name = str(
            copied_values.get("name")
            or (source.printed_name if source is not None else "Copy")
        )
        oracle_id = (
            source.oracle_id
            if source is not None
            else (
                "custom-copy:"
                f"{self._stable_runtime_id('copy-oracle', ref)}"
            )
        )
        public = zone in PUBLIC_ZONES or zone in {
            "battlefield",
            "stack",
        }
        card = CardInstance(
            object_id=object_id,
            ref=ref,
            oracle_id=oracle_id,
            printed_name=name,
            owner=controller,
            controller=controller,
            zone=zone,
            object_kind=object_kind,
            zone_timestamp=self._next_zone_timestamp(),
            active_face=(
                source.active_face if source is not None else None
            ),
            annotations={
                "copy_overrides": copied_values,
                **(
                    {"copied_from": source.object_id}
                    if source is not None
                    else {
                        "token_characteristics": copied_values,
                    }
                ),
            },
            known_to=(
                list(self.seats) if public else [controller]
            ),
            revealed_to=(
                list(self.seats) if public else []
            ),
        )
        self.state.cards[object_id] = card
        if zone != "stack":
            self.state.players[controller].zones[zone].append(
                object_id
            )
        if zone == "battlefield":
            control_history.record_battlefield_acquisition(self.state, card, card.zone_timestamp)
            card.entered_battlefield_turn_sequence = (
                self.state.turn_sequence
            )
            self._refresh_world_supertype_timestamp(
                card,
                gained_at=card.zone_timestamp,
            )
        return card

    def create_card_copy(
        self,
        controller: str,
        source: str,
        *,
        zone: str | None = None,
    ) -> CardInstance:
        """Create a noncard copy for a compiled CR 707 effect.

        Casting that copy during the resolving effect remains a separate
        casting operation. Callers cannot create an unattached stack object.
        """

        original = self._resolve_object(controller, source)
        destination = str(zone or original.zone)
        if destination == "stack":
            raise GameRuleError(
                "A card copy becomes a stack object only through casting"
            )
        return self._create_copy_object(
            controller=controller,
            source=original,
            characteristics=self._copyable_characteristics(original),
            object_kind="card_copy",
            zone=destination,
        )

    def _copy_stack_item(
        self,
        *,
        controller: str,
        target: StackItem,
        targets: Sequence[str],
        target_groups: Mapping[str, Sequence[str]],
        reason: str,
    ) -> StackItem:
        """Create an independent stack copy without copying paid costs."""

        ref = self._next_ref("S")
        original_card = self.state.cards.get(
            target.card_object_id or ""
        )
        original_data = (
            self._copyable_characteristics(original_card)
            if original_card is not None
            else {}
        )
        original_types, _, _ = self._type_parts(
            str(original_data.get("type_line") or "")
        )
        permanent_spell = bool(
            target.kind in {"spell", "spell_copy"}
            and original_types
            and not original_types.intersection({"instant", "sorcery"})
        )
        copy_object = (
            self._create_copy_object(
                controller=controller,
                source=original_card,
                characteristics=(
                    original_data
                    or {
                        "name": target.label,
                        "type_line": "Instant",
                    }
                ),
                object_kind="spell_copy",
                zone="stack",
            )
            if target.kind in {"spell", "spell_copy"}
            else None
        )
        copied = StackItem(
            stack_id=self._stable_runtime_id("stack", ref),
            ref=ref,
            kind=(
                "spell_copy"
                if target.kind in {"spell", "spell_copy"}
                else target.kind
            ),
            controller=controller,
            label=f"{target.label} copy",
            card_object_id=(
                copy_object.object_id
                if copy_object is not None
                else None
            ),
            source_object_id=target.source_object_id,
            semantic_key=target.semantic_key,
            targets=[str(value) for value in targets],
            modes=list(target.modes),
            x_value=target.x_value,
            chosen_face=target.chosen_face,
            notes=target.notes,
            default_destination=target.default_destination,
            visibility=list(self.seats),
            referred_object_ids=list(target.referred_object_ids),
            context={
                **copy.deepcopy(dict(target.context)),
                "target_groups": {
                    str(key): [str(value) for value in values]
                    for key, values in target_groups.items()
                },
                "target_snapshots": {
                    str(value): self._target_snapshot(str(value))
                    for value in targets
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
                "copied_from_stack": target.ref,
                "copy_permanent_spell": permanent_spell,
                "copy_permanent_name": str(
                    original_data.get("name") or target.label
                ),
                "copy_permanent_characteristics": copy.deepcopy(
                    original_data
                ),
            },
        )
        self.state.stack.append(copied)
        self._log(
            controller,
            "stack.copy",
            f"{controller} copied {target.ref} as {copied.ref}.",
            {
                "source_stack": target.ref,
                "copy_stack": copied.ref,
                "targets": list(copied.targets),
                "reason": reason,
            },
            importance=2,
        )
        return copied

    def _complete_arbiter_resolution(self, decision: Any) -> None:
        response = decision.responses["arbiter"]
        action = response.pop("action")
        if not self.state.stack:
            raise GameRuleError("Stack became empty before arbiter resolution")
        item = self.state.stack[-1]
        if action == "counter_as_rule" or action == "fizzle":
            self._counter_stack_item(
                item.ref,
                destination=str(response.get("destination") or "graveyard"),
                reason=action,
                as_rule=True,
                countered_by="arbiter",
            )
            self._grant_priority(self.state.active_player)
            return
        effects = [dict(effect) for effect in response.get("effects") or []]
        destination = response.get("destination", item.default_destination)
        note = str(response.get("note") or "")
        if action == "register_and_resolve":
            key = str(response.get("semantic_key") or item.semantic_key or "")
            if not key:
                raise GameRuleError("A semantic_key is required to register a program")
            self.semantics.put(
                SemanticProgram(
                    key=key,
                    label=item.label,
                    effects=effects,
                    destination=destination,
                    notes=note,
                )
            )
            item.semantic_key = key
        self._begin_resolve_item(item, effects, destination, note=note)

    def _begin_resolve_item(
        self,
        item: StackItem,
        effects: Sequence[Mapping[str, Any]],
        destination: str | None,
        *,
        note: str = "",
    ) -> None:
        if not self.state.stack or self.state.stack[-1] is not item:
            raise GameRuleError(
                "Only the top object of the stack can begin resolving"
            )
        if not self._revalidate_resolution_targets(item):
            return
        self._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(effect) for effect in effects],
            destination=destination,
            note=note,
            instruction_pointer=0,
        )


    def _semantic_frame(
        self,
        item: StackItem,
        *,
        instruction_pointer: int,
        locals: Mapping[str, Any] | None = None,
        pending_choice_id: str | None = None,
    ) -> dict[str, Any]:
        program = self.semantics.get(item.semantic_key)
        return {
            "schema_version": 1,
            "semantic_program_id": item.semantic_key,
            "semantic_program_version": program.version if program else None,
            "stack_object": item.ref,
            "instruction_pointer": instruction_pointer,
            "locals": copy.deepcopy(dict(locals or {})),
            "controller": item.controller,
            "pending_choice_id": pending_choice_id,
        }

    def _validate_semantic_frame(
        self,
        frame: Mapping[str, Any],
        item: StackItem,
    ) -> None:
        if str(frame.get("stack_object") or "") != item.ref:
            raise GameRuleError("Semantic continuation stack object changed")
        if str(frame.get("semantic_program_id") or "") != str(
            item.semantic_key or ""
        ):
            raise GameRuleError("Semantic continuation program changed")
        program = self.semantics.get(item.semantic_key)
        expected_version = program.version if program else None
        if frame.get("semantic_program_version") != expected_version:
            raise GameRuleError("Semantic continuation program version changed")

    def _semantic_value(self, value: Any, item: StackItem) -> Any:
        return resolve_semantic_value(self, value, item)

    @staticmethod
    def _effect_has_missing_target(effect: Mapping[str, Any]) -> bool:
        return any(
            key in effect and effect.get(key) is None
            for key in ("target", "stack", "card", "object")
        )

    def _continue_resolution(
        self,
        *,
        stack_ref: str,
        effects: list[dict[str, Any]],
        destination: str | None,
        note: str,
        instruction_pointer: int = 0,
        entry_replacement_selections: Sequence[
            str | Mapping[str, Any]
        ] = (),
    ) -> None:
        item = next((candidate for candidate in self.state.stack if candidate.ref == stack_ref), None)
        if item is None:
            raise GameRuleError(f"Stack object {stack_ref} no longer exists")
        item.context["currently_resolving"] = True
        index = 0
        while index < len(effects):
            effect = normalize_game_record_v3_effect(
                self._semantic_value(effects[index], item)
            )
            if self._effect_has_missing_target(effect):
                self._log(
                    item.controller,
                    "effect.target.skipped",
                    f"Skipped a target-dependent part of {item.ref}.",
                    {
                        "stack": item.ref,
                        "operation": effect.get("op"),
                        "reason": "that target is illegal",
                    },
                    importance=1,
                )
                index += 1
                continue
            if effect.get("op") == "choose_cards_apnap":
                self._issue_apnap_choice(
                    effect=effect,
                    continuation={
                        "stack_ref": stack_ref,
                        "effects": effects[index + 1 :],
                        "destination": destination,
                        "note": note,
                        "semantic_frame": self._semantic_frame(
                            item,
                            instruction_pointer=instruction_pointer + index,
                        ),
                    },
                )
                return
            if effect.get("op") == "search":
                self._begin_semantic_search(
                    item=item,
                    effect=effect,
                    remaining=effects[index + 1 :],
                    destination=destination,
                    note=note,
                    instruction_pointer=instruction_pointer + index,
                )
                return
            try:
                typed_plan = default_semantic_interpreter().lower_for_seats(
                    effect,
                    actor=item.controller,
                    default_reason=item.label,
                    seats=self.seats,
                    active_seats=self.active_seats,
                    apnap_order=self.apnap_order(), source=semantic_source_context(item, self.state.cards),
                )
            except SemanticNodeError as exc:
                raise GameRuleError(str(exc)) from exc
            replacement_frame = (effects[index + 1 :], destination, note, instruction_pointer + index)
            if typed_plan is not None:
                draw_request = prepare_draw_resolution(
                    typed_plan,
                    tuple(effects[index + 1 :]),
                )
                if draw_request is not None:
                    if draw_request.current is None:
                        index += 1
                        continue
                    self._begin_draw_sequence(
                        draw_request.current.player,
                        draw_request.current.count,
                        reason=draw_request.current.reason,
                        private=draw_request.current.private,
                        post_draw_actions=draw_request.current.post_draw_actions,
                        continuation={
                            "kind": "semantic_resolution",
                            "stack_ref": stack_ref,
                            "effects": list(draw_request.remaining_effects),
                            "destination": destination,
                            "note": note,
                            "instruction_pointer": instruction_pointer + index + 1,
                        },
                    )
                    return
                if not apply_effect_with_replacement_choice(self, item, effect, replacement_frame, plan=typed_plan):
                    return
                index += 1
                continue
            if (
                str(effect.get("op") or "")
                in default_semantic_choice_registry().operations
            ):
                self._begin_semantic_choice(
                    item=item,
                    effect=effect,
                    remaining=effects[index + 1 :],
                    destination=destination,
                    note=note,
                    instruction_pointer=instruction_pointer + index,
                )
                return
            if not apply_effect_with_replacement_choice(self, item, effect, replacement_frame):
                return
            index += 1
        # Prepare the final physical zone move before removing the resolving
        # stack object. Intrinsic as-enters counters are self-replacements in
        # this same immutable event tree, so a counter-replacement ordering
        # choice can suspend without replaying prior instructions.
        entry_preparation = prepare_resolving_entry_replacement(
            self,
            item=item,
            destination=destination,
            note=note,
            instruction_pointer=instruction_pointer + len(effects),
            selections=entry_replacement_selections,
            error_type=GameRuleError,
        )
        if entry_preparation.suspended:
            return
        entry_destination = entry_preparation.destination
        prepared_entry = entry_preparation.replacement

        complete_stack_resolution(
            self,
            item=item,
            destination=entry_destination,
            prepared_replacement=prepared_entry,
        )
        self._log(item.controller, "stack.resolve", f"Resolved {item.ref} {item.label}.", {"stack": item.ref, "effects": effects, "destination": destination, "note": note}, importance=2, changed_players=[item.controller])
        if self._stabilize():
            return
        self._grant_priority(self.state.active_player)

    def _stack_item_can_be_countered(self, item: StackItem) -> bool:
        return stack_item_can_be_countered(self, item)

    def _counter_stack_item(
        self,
        value: str,
        *,
        destination: str = "graveyard",
        reason: str = "countered",
        as_rule: bool = False,
        countered_by: str | None = None,
    ) -> StackItem:
        return counter_stack_item(
            self,
            value,
            destination=destination,
            reason=reason,
            as_rule=as_rule,
            countered_by=countered_by,
        )

    # ------------------------------------------------------------------
    # Replacement/prevention ordering during resolution
    # ------------------------------------------------------------------





    # ------------------------------------------------------------------
    # Combat with multiple defenders
    # ------------------------------------------------------------------
    def _attack_declaration_error(
        self,
        card: CardInstance,
        active: str,
    ) -> str | None:
        data = self._effective_card_data(card)
        card_types, _, _ = self._type_parts(
            str(data.get("type_line") or "")
        )
        if card.controller != active:
            return f"{card.ref} is not controlled by {active}"
        if card.phased_out:
            return f"{card.ref} is phased out"
        if "creature" not in card_types:
            return f"{card.ref} is not a creature"
        if "battle" in card_types:
            return f"{card.ref} cannot attack because it is a Battle"
        if card.tapped:
            return f"{card.ref} is tapped"
        if haste.summoning_sickness_prohibits_attack(self, card):
            return f"{card.ref} is summoning sick"
        if defender.defender_prohibits_attack(data):
            return f"{card.ref} has defender and cannot attack"
        return None

    def _combat_keywords(self, card: CardInstance) -> frozenset[str]:
        return normalized_keywords(
            self._effective_card_data(card).get("keywords", [])
        )

    def _combat_damage_participants(self) -> list[CardInstance]:
        object_ids = set(self.state.combat.attackers)
        object_ids.update(
            blocker_id
            for blocker_ids in self.state.combat.blockers.values()
            for blocker_id in blocker_ids
        )
        participants: list[CardInstance] = []
        for object_id in sorted(object_ids):
            card = self.state.cards.get(object_id)
            if (
                card is None
                or card.zone != "battlefield"
                or card.phased_out
            ):
                continue
            card_types, _, _ = self._type_parts(
                str(
                    self._effective_card_data(card).get("type_line")
                    or ""
                )
            )
            if "creature" in card_types and "battle" not in card_types:
                participants.append(card)
        return participants

    def _initialize_combat_damage_steps(self) -> None:
        combat = self.state.combat
        if combat.damage_step_initialized:
            return
        keywords_by_object = {
            card.object_id: self._combat_keywords(card)
            for card in self._combat_damage_participants()
        }
        combat.first_strike_step = first_strike_step_required(
            keywords_by_object
        )
        combat.ordinary_second_damage_combatants = sorted(
            ordinary_second_step_combatants(keywords_by_object)
            if combat.first_strike_step
            else ()
        )
        combat.damage_step_initialized = True

    def _assigns_combat_damage_this_step(
        self,
        card: CardInstance,
    ) -> bool:
        combat = self.state.combat
        return assigns_in_damage_step(
            object_id=card.object_id,
            current_keywords=self._combat_keywords(card),
            step_index=combat.damage_step_index,
            first_strike_step=combat.first_strike_step,
            ordinary_second_step=frozenset(
                combat.ordinary_second_damage_combatants
            ),
        )

    def _source_colors_for_ref(self, source_ref: str | None) -> set[str]:
        data = source_characteristics_for_ref(self, source_ref)
        return {
            str(color).upper()
            for color in (data or {}).get("colors", ())
        }

    def _can_block(
        self,
        attacker: CardInstance,
        blocker: CardInstance,
    ) -> tuple[bool, str | None]:
        attacker_data = self._effective_card_data(attacker)
        blocker_data = self._effective_card_data(blocker)
        blocker_types, _, _ = self._type_parts(
            str(blocker_data.get("type_line") or "")
        )
        if "battle" in blocker_types:
            return False, "blocker_is_battle"
        by_ref = {card.ref: card for card in self.state.cards.values()}
        for participant in current_declaration_restrictions(
            self,
            error_type=GameRuleError,
        ):
            source = participant.source
            for template in (participant.template,):
                if (
                    "block" not in template.declarations
                    or template.mode != "prohibit"
                ):
                    continue
                applies = {
                    "self": source.object_id == blocker.object_id,
                    "attached": source.attached_to == blocker.object_id,
                    "attached_option": (
                        source.attached_to == attacker.object_id
                    ),
                    "source_opponents": (
                        blocker.controller != source.controller
                    ),
                    "source_option": source.object_id == attacker.object_id,
                    "global": True,
                }[template.scope]
                if not applies:
                    continue
                if not self._declaration_option_matches_relation(
                    template,
                    kind="block",
                    source=source,
                    option=attacker.ref,
                    by_ref=by_ref,
                ):
                    continue
                if template.condition is not None and (
                    self._declaration_condition_holds(
                        template.condition,
                        kind="block",
                        source=source,
                        variable=blocker.ref,
                        option=attacker.ref,
                        by_ref=by_ref,
                    )
                    != template.applies_when_condition
                ):
                    continue
                if (
                    self._matches_declaration_predicate(
                        blocker, template.subject, source=source
                    )
                    and self._matches_declaration_predicate(
                        attacker, template.opposing, source=source
                    )
                ):
                    return (
                        False,
                        f"declaration_restriction:{template.template_id}",
                    )
        evasion = engine_combat_evasion_verdict(
            self, attacker, blocker, blocker.controller
        )
        if not evasion.allowed:
            return False, evasion.reason
        if protection_verdict(
            attacker_data,
            ProtectionSource.from_characteristics(blocker_data),
        ) is not ProtectionVerdict.ALLOWED:
            return False, "attacker_has_protection"
        return True, None

    def _active_goad_designations(
        self,
        card: CardInstance,
    ) -> tuple[GoadDesignation, ...]:
        return tuple(
            designation
            for designation in card.goaded_by
            if designation.player in self.state.players
            and self.state.players[
                designation.player
            ].turns_begun < designation.expires_at_turns_begun
        )

    def _goad_prohibition_source(
        self,
        card: CardInstance,
    ) -> CardInstance | None:
        """Return a trusted typed static source that forbids goading."""

        for prohibition in active_goad_prohibitions(self):
            if prohibition.source_controller != card.controller:
                continue
            for source in self.state.cards.values():
                if source.ref == prohibition.source_ref:
                    return source
        return None

    @staticmethod
    def _declaration_cost(
        *,
        cost_id: str,
        variable: str,
        option: str,
        payer: str,
        mana: tuple[tuple[str, int], ...],
        source: CardInstance,
        label: str,
    ) -> DeclarationCost:
        return DeclarationCost(
            cost_id=cost_id,
            variable=variable,
            option=option,
            payer=payer,
            mana=mana,
            source=source.ref,
            label=label,
        )

    def _declaration_costs(
        self,
        kind: str,
        payer: str,
        domains: Mapping[str, Sequence[str]],
    ) -> tuple[
        tuple[DeclarationCost, ...],
        tuple[tuple[CardInstance, str], ...],
    ]:
        """Derive a represented CR 508.1h or 509.1d locked-cost set."""

        costs: list[DeclarationCost] = []
        by_ref = {card.ref: card for card in self.state.cards.values()}
        for source in sorted(
            self.state.cards.values(), key=lambda value: value.ref
        ):
            if source.zone != "battlefield" or source.phased_out:
                continue
            for template_index, template in enumerate(
                declaration_cost_specs(
                    self._effective_ability_fragments(
                        source,
                        error_type=GameRuleError,
                    )
                )
            ):
                if kind not in template.declarations:
                    continue

                def source_planeswalker(option: str) -> bool:
                    target = by_ref.get(option)
                    if (
                        target is None
                        or target.controller != source.controller
                    ):
                        return False
                    target_types, _, _ = self._type_parts(
                        str(
                            self._effective_card_data(target).get(
                                "type_line"
                            )
                            or ""
                        )
                    )
                    return "planeswalker" in target_types

                selections: list[tuple[str, str]] = []
                if template.scope == "self" and source.ref in domains:
                    selections.extend(
                        (source.ref, str(option))
                        for option in domains[source.ref]
                    )
                elif template.scope == "attached":
                    attached = self.state.cards.get(
                        source.attached_to or ""
                    )
                    if attached is not None and attached.ref in domains:
                        selections.extend(
                            (attached.ref, str(option))
                            for option in domains[attached.ref]
                        )
                elif (
                    template.scope == "source_controller"
                    and kind == "attack"
                ):
                    for variable, options in sorted(domains.items()):
                        selections.extend(
                            (variable, str(option))
                            for option in options
                            if option == source.controller
                            or (
                                template.includes_planeswalkers
                                and source_planeswalker(str(option))
                            )
                        )
                elif (
                    template.scope == "source_planeswalkers"
                    and kind == "attack"
                ):
                    selections.extend(
                        (variable, str(option))
                        for variable, options in sorted(domains.items())
                        for option in options
                        if source_planeswalker(str(option))
                    )
                elif template.scope == "global" and kind == "block":
                    selections.extend(
                        (variable, str(option))
                        for variable, options in sorted(domains.items())
                        for option in options
                    )
                if not selections:
                    continue
                if (
                    template.source_condition == "source_untapped"
                    and source.tapped
                ):
                    continue
                if (
                    template.source_condition == "source_attacking"
                    and not source.attacking
                ):
                    continue
                for variable, option in selections:
                    costs.append(
                        self._declaration_cost(
                            cost_id=(
                                f"{kind}-cost:{template.scope}:{source.ref}:"
                                f"{template_index}:{variable}:{option}"
                            ),
                            variable=variable,
                            option=option,
                            payer=payer,
                            mana=template.mana,
                            source=source,
                            label=(
                                f"{self.display_name(source.object_id)} "
                                f"requires {template.printed_cost} for "
                                f"{variable} to {kind}."
                            ),
                        )
                    )
        return tuple(costs), ()

    def _attack_declaration_costs(
        self,
        active: str,
        domains: Mapping[str, Sequence[str]],
    ) -> tuple[
        tuple[DeclarationCost, ...],
        tuple[tuple[CardInstance, str], ...],
    ]:
        return self._declaration_costs("attack", active, domains)

    def _matches_declaration_predicate(
        self,
        card: CardInstance,
        predicate: DeclarationObjectPredicate,
        *,
        source: CardInstance,
    ) -> bool:
        data = self._effective_card_data(card)
        card_types, subtypes, supertypes = self._type_parts(
            str(data.get("type_line") or "")
        )
        normalized_types = {value.casefold() for value in card_types}
        if predicate.types_any and not normalized_types.intersection(
            value.casefold() for value in predicate.types_any
        ):
            return False
        if predicate.types_none and normalized_types.intersection(
            value.casefold() for value in predicate.types_none
        ):
            return False
        normalized_supertypes = {
            value.casefold() for value in supertypes
        }
        if predicate.supertypes_any and not normalized_supertypes.intersection(
            value.casefold() for value in predicate.supertypes_any
        ):
            return False
        if predicate.supertypes_none and normalized_supertypes.intersection(
            value.casefold() for value in predicate.supertypes_none
        ):
            return False
        normalized_subtypes = {value.casefold() for value in subtypes}
        if predicate.subtypes_any and not normalized_subtypes.intersection(
            value.casefold() for value in predicate.subtypes_any
        ):
            return False
        if predicate.subtypes_none and normalized_subtypes.intersection(
            value.casefold() for value in predicate.subtypes_none
        ):
            return False
        colors = {
            str(value).upper() for value in data.get("colors", [])
        }
        if predicate.colors_any and not colors.intersection(
            str(value).upper() for value in predicate.colors_any
        ):
            return False
        if predicate.colors_none and colors.intersection(
            str(value).upper() for value in predicate.colors_none
        ):
            return False
        keywords = normalized_keywords(data.get("keywords", []))
        if predicate.keywords_any and not keywords.intersection(
            str(value).casefold() for value in predicate.keywords_any
        ):
            return False
        if predicate.keywords_none and keywords.intersection(
            str(value).casefold() for value in predicate.keywords_none
        ):
            return False
        if predicate.token is not None and card.is_token != predicate.token:
            return False
        if predicate.goaded is not None:
            goaded = bool(self._active_goad_designations(card))
            if goaded != predicate.goaded:
                return False
        if predicate.tapped is not None and card.tapped != predicate.tapped:
            return False
        if predicate.enchanted is not None:
            enchanted = False
            for attachment_id in card.attachments:
                attachment = self.state.cards.get(attachment_id)
                if (
                    attachment is None
                    or attachment.zone != "battlefield"
                    or attachment.phased_out
                    or attachment.attached_to != card.object_id
                ):
                    continue
                _, attachment_subtypes, _ = self._type_parts(
                    str(
                        self._effective_card_data(attachment).get("type_line")
                        or ""
                    )
                )
                if "aura" in attachment_subtypes:
                    enchanted = True
                    break
            if enchanted != predicate.enchanted:
                return False
        for comparison_rule in (
            *((predicate.stat,) if predicate.stat is not None else ()),
            *predicate.additional_stats,
        ):
            left = self._numeric_stat(
                card.object_id, comparison_rule.stat
            )
            right = (
                int(comparison_rule.value or 0)
                if comparison_rule.operand == "fixed"
                else self._numeric_stat(
                    source.object_id, comparison_rule.stat
                )
            )
            comparison = {
                "eq": left == right,
                "lt": left < right,
                "le": left <= right,
                "gt": left > right,
                "ge": left >= right,
            }[comparison_rule.operator]
            if not comparison:
                return False
        return True

    def _restriction_variables(
        self,
        template: DeclarationRestrictionTemplate,
        source: CardInstance,
        domains: Mapping[str, Sequence[str]],
    ) -> tuple[str, ...]:
        if template.scope == "self":
            return (source.ref,) if source.ref in domains else ()
        if template.scope == "attached":
            attached = self.state.cards.get(source.attached_to or "")
            return (
                (attached.ref,)
                if attached is not None and attached.ref in domains
                else ()
            )
        if template.scope == "attached_option":
            return tuple(sorted(domains))
        if template.scope == "source_opponents":
            by_ref = {
                card.ref: card for card in self.state.cards.values()
            }
            return tuple(
                variable
                for variable in sorted(domains)
                if variable in by_ref
                and by_ref[variable].controller != source.controller
            )
        return tuple(sorted(domains))

    def _restriction_is_relevant(
        self,
        scope: str | None,
        source: CardInstance,
        domains: Mapping[str, Sequence[str]],
    ) -> bool:
        if not domains:
            return False
        if scope == "self":
            return source.ref in domains
        if scope == "attached":
            attached = self.state.cards.get(source.attached_to or "")
            return attached is not None and attached.ref in domains
        if scope == "attached_option":
            attached = self.state.cards.get(source.attached_to or "")
            return attached is not None and any(
                attached.ref in options for options in domains.values()
            )
        if scope == "source_opponents":
            return any(
                card.ref in domains and card.controller != source.controller
                for card in self.state.cards.values()
            )
        if scope == "source_option":
            return any(source.ref in options for options in domains.values())
        return True

    def _declaration_condition_player(
        self,
        role: DeclarationConditionPlayer,
        *,
        kind: str,
        source: CardInstance,
        variable: str,
        option: str,
        by_ref: Mapping[str, CardInstance],
    ) -> str | None:
        if role == "source_controller":
            return source.controller
        if kind == "attack":
            if role == "attacking_player":
                attacker = by_ref.get(variable)
                return attacker.controller if attacker is not None else None
            return self._defending_player_for_attack_target(option)
        if role == "attacking_player":
            attacker = by_ref.get(option)
            return attacker.controller if attacker is not None else None
        blocker = by_ref.get(variable)
        return blocker.controller if blocker is not None else None

    def _declaration_battlefield_count(
        self,
        condition: DeclarationBattlefieldCondition,
        *,
        player: str,
        source: CardInstance,
        exclude_source: bool,
    ) -> int:
        return sum(
            1
            for card in self.state.cards.values()
            if card.zone == "battlefield"
            and not card.phased_out
            and card.controller == player
            and (not exclude_source or card.object_id != source.object_id)
            and any(
                self._matches_declaration_predicate(
                    card,
                    predicate,
                    source=source,
                )
                for predicate in condition.predicates_any
            )
        )

    def _declaration_condition_holds(
        self,
        condition: DeclarationCondition,
        *,
        kind: str,
        source: CardInstance,
        variable: str,
        option: str,
        by_ref: Mapping[str, CardInstance],
    ) -> bool:
        if isinstance(condition, DeclarationCombatCondition):
            return (
                condition.kind == "attacking_alone"
                and len(self._current_attacker_cards()) == 1
            )
        if isinstance(condition, DeclarationPlayerStateCondition):
            player = self._declaration_condition_player(
                condition.player,
                kind=kind,
                source=source,
                variable=variable,
                option=option,
                by_ref=by_ref,
            )
            if player is None:
                return False
            if condition.state == "monarch":
                return self.state.monarch == player
            return self.state.players[player].poison > 0
        if isinstance(condition, DeclarationTurnHistoryCondition):
            if condition.fact == "attacked_player":
                if kind != "attack" or option not in self.active_seats:
                    return False
                return self._object_attacked_player_this_turn(
                    source.logical_object_id,
                    option,
                )
            if condition.player is None:
                return False
            player = self._declaration_condition_player(
                condition.player,
                kind=kind,
                source=source,
                variable=variable,
                option=option,
                by_ref=by_ref,
            )
            if player is None:
                return False
            if condition.fact == "cast_spell":
                return self._player_cast_spell_this_turn(player)
            if condition.fact == "cast_creature_spell":
                return self._player_cast_spell_this_turn(
                    player,
                    creature=True,
                )
            if condition.fact == "cast_noncreature_spell":
                return self._player_cast_spell_this_turn(
                    player,
                    creature=False,
                )
            if condition.fact == "creature_died_under_control":
                return self._creature_died_under_control_this_turn(player)
            if condition.fact == "opponent_dealt_damage":
                return opponent_was_dealt_damage_this_turn(
                    self.state.turn_history,
                    turn_sequence=self.state.turn_sequence,
                    player=player,
                    active_players=self.active_seats,
                )
            return False
        if isinstance(condition, DeclarationSharedSubtypeCondition):
            player = self._declaration_condition_player(
                condition.player,
                kind=kind,
                source=source,
                variable=variable,
                option=option,
                by_ref=by_ref,
            )
            if player is None:
                return False
            subtype_counts: dict[str, int] = {}
            for card in self.state.cards.values():
                if (
                    card.zone != "battlefield"
                    or card.phased_out
                    or card.controller != player
                ):
                    continue
                data = self._effective_card_data(card)
                card_types, subtypes, _ = self._type_parts(
                    str(data.get("type_line") or "")
                )
                if "creature" not in card_types:
                    continue
                for subtype in subtypes:
                    subtype_counts[subtype] = (
                        subtype_counts.get(subtype, 0) + 1
                    )
            return any(
                count >= condition.minimum
                for count in subtype_counts.values()
            )
        player = self._declaration_condition_player(
            condition.player,
            kind=kind,
            source=source,
            variable=variable,
            option=option,
            by_ref=by_ref,
        )
        if player is None:
            return False
        count = self._declaration_battlefield_count(
            condition,
            player=player,
            source=source,
            exclude_source=condition.exclude_source,
        )
        if condition.compare_player is None:
            return count >= condition.minimum and (
                condition.maximum is None or count <= condition.maximum
            )
        other = self._declaration_condition_player(
            condition.compare_player,
            kind=kind,
            source=source,
            variable=variable,
            option=option,
            by_ref=by_ref,
        )
        if other is None:
            return False
        other_count = self._declaration_battlefield_count(
            condition,
            player=other,
            source=source,
            exclude_source=False,
        )
        return count > other_count

    def _declaration_option_matches_relation(
        self,
        template: DeclarationRestrictionTemplate,
        *,
        kind: str,
        source: CardInstance,
        option: str,
        by_ref: Mapping[str, CardInstance],
    ) -> bool:
        """Return whether an option is in a represented source-relative scope."""

        if template.option_relation is None:
            return True
        if template.option_relation != "source_controller":
            return False
        if kind == "block":
            opposing = by_ref.get(option)
            return (
                opposing is not None
                and opposing.controller == source.controller
            )
        if option == source.controller:
            return True
        if not template.includes_planeswalkers:
            return False
        target = by_ref.get(option)
        if target is None or target.controller != source.controller:
            return False
        target_types, _, _ = self._type_parts(
            str(self._effective_card_data(target).get("type_line") or "")
        )
        return "planeswalker" in target_types

    def _declaration_restrictions(
        self,
        kind: str,
        domains: Mapping[str, Sequence[str]],
    ) -> tuple[
        dict[str, tuple[str, ...]],
        tuple[DeclarationRestriction, ...],
        tuple[tuple[CardInstance, str], ...],
    ]:
        """Apply represented static CR 508.1c/509.1b restrictions."""

        original = {
            str(variable): tuple(str(option) for option in options)
            for variable, options in domains.items()
        }
        remaining = {
            variable: list(options)
            for variable, options in original.items()
        }
        constraints: list[DeclarationRestriction] = []
        by_ref = {card.ref: card for card in self.state.cards.values()}
        for participant in current_declaration_restrictions(
            self,
            error_type=GameRuleError,
        ):
            source = participant.source
            participant_id = participant.participant_id
            for template in (participant.template,):
                if kind not in template.declarations:
                    continue
                if not self._restriction_is_relevant(
                    template.scope, source, original
                ):
                    continue
                variables = self._restriction_variables(
                    template, source, original
                )
                if template.mode == "maximum_total_selections":
                    constraints.append(
                        DeclarationRestriction(
                            restriction_id=(
                                f"{kind}:restriction:{participant_id}:maximum"
                            ),
                            kind="maximum_total_selections",
                            count=template.count,
                            label=(
                                f"{self.display_name(source.object_id)} "
                                f"allows at most {template.count} creature(s) "
                                f"to {kind}."
                            ),
                        )
                    )
                    continue
                if template.mode in {
                    "minimum_option_uses",
                    "maximum_option_uses",
                }:
                    constrained_option = (
                        source.controller
                        if kind == "attack"
                        and template.option_relation == "source_controller"
                        else source.ref
                    )
                    if kind == "attack":
                        constraint_label = (
                            f"{self.display_name(source.object_id)} allows at "
                            f"most {template.count} creature(s) to attack "
                            + (
                                "it."
                                if template.scope == "source_option"
                                else "its controller."
                            )
                        )
                    elif template.mode == "minimum_option_uses":
                        constraint_label = (
                            f"{self.display_name(source.object_id)} requires "
                            f"{template.count} blocker(s) when blocked."
                        )
                    else:
                        constraint_label = (
                            f"{self.display_name(source.object_id)} allows at "
                            f"most {template.count} blocker(s)."
                        )
                    constraints.append(
                        DeclarationRestriction(
                            restriction_id=(
                                f"{kind}:restriction:{participant_id}:option-uses"
                            ),
                            kind=template.mode,
                            option=constrained_option,
                            count=template.count,
                            when_used=(
                                template.mode == "minimum_option_uses"
                            ),
                            label=constraint_label,
                        )
                    )
                    continue
                for variable in variables:
                    subject = by_ref.get(variable)
                    if subject is None or not self._matches_declaration_predicate(
                        subject, template.subject, source=source
                    ):
                        continue
                    if template.mode == "minimum_total_selections":
                        constraints.append(
                            DeclarationRestriction(
                                restriction_id=(
                                    f"{kind}:restriction:{participant_id}:"
                                    f"{variable}:minimum"
                                ),
                                kind="minimum_total_selections",
                                count=template.count,
                                trigger_variable=variable,
                                label=(
                                    f"{self.display_name(subject.object_id)} "
                                    f"can't {kind} alone."
                                ),
                            )
                        )
                        continue
                    if template.mode == "minimum_matching_selections":
                        matching_variables = tuple(
                            candidate
                            for candidate in sorted(original)
                            if candidate != variable
                            and (matching := by_ref.get(candidate)) is not None
                            and self._matches_declaration_predicate(
                                matching,
                                template.matching,
                                source=source,
                            )
                        )
                        constraints.append(
                            DeclarationRestriction(
                                restriction_id=(
                                    f"{kind}:restriction:{participant_id}:"
                                    f"{variable}:matching"
                                ),
                                kind="minimum_variable_selections",
                                count=template.count,
                                trigger_variable=variable,
                                variables=matching_variables,
                                label=(
                                    f"{self.display_name(subject.object_id)} "
                                    f"requires {template.count} matching "
                                    f"creature(s) to also {kind}."
                                ),
                            )
                        )
                        continue
                    legal_options: list[str] = []
                    for option in remaining.get(variable, []):
                        attached_option = (
                            self.state.cards.get(source.attached_to or "")
                            if template.scope == "attached_option"
                            else None
                        )
                        if (
                            template.scope == "source_option"
                            and option != source.ref
                        ):
                            legal_options.append(option)
                            continue
                        if (
                            template.scope == "attached_option"
                            and (
                                attached_option is None
                                or option != attached_option.ref
                            )
                        ):
                            legal_options.append(option)
                            continue
                        if not self._declaration_option_matches_relation(
                            template,
                            kind=kind,
                            source=source,
                            option=option,
                            by_ref=by_ref,
                        ):
                            legal_options.append(option)
                            continue
                        if template.condition is not None:
                            condition_holds = self._declaration_condition_holds(
                                template.condition,
                                kind=kind,
                                source=source,
                                variable=variable,
                                option=option,
                                by_ref=by_ref,
                            )
                            if (
                                condition_holds
                                != template.applies_when_condition
                            ):
                                legal_options.append(option)
                                continue
                        opposing = by_ref.get(option)
                        if (
                            opposing is not None
                            and not self._matches_declaration_predicate(
                                opposing,
                                template.opposing,
                                source=source,
                            )
                        ):
                            legal_options.append(option)
                            continue
                        if (
                            opposing is None
                            and template.opposing != DeclarationObjectPredicate()
                        ):
                            legal_options.append(option)
                    remaining[variable] = legal_options
        return (
            {
                variable: tuple(options)
                for variable, options in remaining.items()
                if options
            },
            tuple(constraints),
            (),
        )

    def _selected_declaration_mana(
        self,
        costs: Sequence[DeclarationCost],
        declaration: Mapping[str, str],
        *,
        payer: str,
    ) -> tuple[dict[str, int], tuple[DeclarationCost, ...]]:
        requirements = {
            key: 0
            for key in ("GENERIC", "W", "U", "B", "R", "G", "C")
        }
        selected: list[DeclarationCost] = []
        for cost in costs:
            if declaration.get(cost.variable) != cost.option:
                continue
            if cost.payer != payer:
                raise GameRuleError(
                    "A declaration cost named a different payer"
                )
            selected.append(cost)
            for key, amount in cost.mana_requirements().items():
                requirements[key] += amount
        return requirements, tuple(selected)

    def _attack_declaration_components(
        self,
        active: str,
    ) -> tuple[
        DeclarationProblem,
        tuple[DeclarationCost, ...],
        tuple[tuple[CardInstance, str, str], ...],
    ]:
        planeswalkers = self._attackable_planeswalkers(active)
        battles = self._attackable_battles(active)
        planeswalker_ids = {walker["id"] for walker in planeswalkers}
        defenders = [
            *[seat for seat in self.active_seats if seat != active],
            *[walker["id"] for walker in planeswalkers],
            *[
                battle["id"]
                for battle in battles
                if battle["id"] not in planeswalker_ids
            ],
        ]
        domains: dict[str, tuple[str, ...]] = {}
        requirements: list[DeclarationRequirement] = []
        for object_id in self.state.players[active].zones["battlefield"]:
            card = self.state.cards[object_id]
            if self._attack_declaration_error(card, active) is not None:
                continue
            domains[card.ref] = tuple(defenders)
            for requirement_index, requirement in enumerate(
                declaration_requirement_specs(
                    self._effective_ability_fragments(
                        card,
                        error_type=GameRuleError,
                    )
                )
            ):
                if requirement.kind != "attack_each_combat":
                    continue
                requirements.append(
                    DeclarationRequirement(
                        requirement_id=(
                            f"attack:{card.ref}:each-combat:"
                            f"{requirement_index}"
                        ),
                        kind="choose",
                        variable=card.ref,
                        label=(
                            f"{self.display_name(card.object_id)} attacks "
                            "this combat if able."
                        ),
                    )
                )
            for designation in self._active_goad_designations(card):
                requirements.extend(
                    (
                        DeclarationRequirement(
                            requirement_id=(
                                f"attack:{card.ref}:goad:{designation.player}:attack"
                            ),
                            kind="choose",
                            variable=card.ref,
                            label=(
                                f"{self.display_name(card.object_id)} attacks "
                                f"this combat if able because {designation.player} "
                                "goaded it."
                            ),
                        ),
                        DeclarationRequirement(
                            requirement_id=(
                                f"attack:{card.ref}:goad:{designation.player}:other"
                            ),
                            kind="choose_option_in",
                            variable=card.ref,
                            options=tuple(
                                seat
                                for seat in self.active_seats
                                if seat not in {active, designation.player}
                            ),
                            label=(
                                f"{self.display_name(card.object_id)} attacks "
                                f"a player other than {designation.player} if able."
                            ),
                        ),
                    )
                )
        domains, restrictions, restriction_gaps = (
            self._declaration_restrictions("attack", domains)
        )
        costs, cost_gaps = self._attack_declaration_costs(
            active,
            domains,
        )
        problem = DeclarationProblem(
            domains=domains,
            requirements=tuple(requirements),
            restrictions=restrictions,
            costed_options=frozenset(cost.selection for cost in costs),
        )
        gaps = tuple(
            (source, line, "restriction")
            for source, line in restriction_gaps
        ) + tuple(
            (source, line, "cost") for source, line in cost_gaps
        )
        return problem, costs, gaps

    def _attack_declaration_problem(self, active: str) -> DeclarationProblem:
        return self._attack_declaration_components(active)[0]

    def _block_declaration_costs(
        self,
        defender: str,
        domains: Mapping[str, Sequence[str]],
    ) -> tuple[
        tuple[DeclarationCost, ...],
        tuple[tuple[CardInstance, str], ...],
    ]:
        return self._declaration_costs("block", defender, domains)

    def _block_declaration_components(
        self,
        defender: str,
    ) -> tuple[
        DeclarationProblem,
        tuple[DeclarationCost, ...],
        tuple[tuple[CardInstance, str, str], ...],
        tuple[menace.MenaceBlockRestriction, ...],
    ]:
        attacker_cards = [
            card
            for card in self._current_attacker_cards()
            if self._defending_player_for_attacker(
                card.object_id,
                self.state.combat.attackers[card.object_id],
            )
            == defender
        ]
        domains: dict[str, tuple[str, ...]] = {}
        blockers_by_ref: dict[str, CardInstance] = {}
        for object_id in self.state.players[defender].zones["battlefield"]:
            blocker = self.state.cards[object_id]
            blocker_types, _, _ = self._type_parts(
                str(
                    self._effective_card_data(blocker).get("type_line")
                    or ""
                )
            )
            if (
                blocker.controller != defender
                or blocker.tapped
                or blocker.phased_out
                or "creature" not in blocker_types
                or "battle" in blocker_types
            ):
                continue
            legal = tuple(
                attacker.ref
                for attacker in attacker_cards
                if self._can_block(attacker, blocker)[0]
            )
            if legal:
                domains[blocker.ref] = legal
                blockers_by_ref[blocker.ref] = blocker

        requirements = typed_blocker_requirements(
            self,
            blockers_by_ref,
            error_type=GameRuleError,
        )

        restrictions: list[DeclarationRestriction] = []
        menace_restrictions: list[menace.MenaceBlockRestriction] = []
        for attacker in attacker_cards:
            requirements.extend(
                typed_attacker_block_requirements(
                    self,
                    attacker,
                    domains,
                    blockers_by_ref,
                    error_type=GameRuleError,
                )
            )
            current_menace = menace.current_menace_restriction(
                self._effective_card_data(attacker),
                attacker.ref,
                is_attacking=(
                    attacker.object_id in self.state.combat.attackers
                ),
            )
            if current_menace is not None:
                menace_restrictions.append(current_menace)
                restrictions.append(
                    current_menace.declaration_restriction()
                )
        domains, static_restrictions, restriction_gaps = (
            self._declaration_restrictions("block", domains)
        )
        restrictions.extend(static_restrictions)
        costs, cost_gaps = self._block_declaration_costs(
            defender,
            domains,
        )
        problem = DeclarationProblem(
            domains=domains,
            requirements=tuple(requirements),
            restrictions=tuple(restrictions),
            costed_options=frozenset(cost.selection for cost in costs),
        )
        gaps = tuple(
            (source, line, "restriction")
            for source, line in restriction_gaps
        ) + tuple(
            (source, line, "cost") for source, line in cost_gaps
        )
        return problem, costs, gaps, tuple(menace_restrictions)

    def _block_declaration_problem(
        self,
        defender: str,
    ) -> DeclarationProblem:
        return self._block_declaration_components(defender)[0]

    @staticmethod
    def _validate_declaration_requirements(
        problem: DeclarationProblem,
        declaration: Mapping[str, str],
    ) -> None:
        try:
            evaluation = problem.evaluate(declaration)
        except (DeclarationConstraintError, DeclarationSearchLimitError) as exc:
            raise GameRuleError(str(exc)) from exc
        if evaluation.restriction_errors:
            raise GameRuleError(evaluation.restriction_errors[0])
        if len(evaluation.satisfied) != evaluation.maximum:
            raise GameRuleError(
                "Combat declaration satisfies "
                f"{len(evaluation.satisfied)} of a possible "
                f"{evaluation.maximum} requirements"
            )

    def _issue_attackers(self) -> None:
        active = self.state.active_player
        if active not in self.active_seats:
            self._advance_step()
            return
        candidate_by_ref: dict[str, dict[str, Any]] = {}
        for oid in self.state.players[active].zones["battlefield"]:
            card = self.state.cards[oid]
            data = self._effective_card_data(card)
            if self._attack_declaration_error(card, active) is None:
                candidate_by_ref[card.ref] = {
                    "id": card.ref,
                    "name": self.display_name(oid),
                    "sick": haste.is_summoning_sick(self, card),
                    "haste": haste.has_effective_haste(self, card),
                }
        problem, costs, unresolved = self._attack_declaration_components(
            active
        )
        if unresolved:
            source, line, category = unresolved[0]
            self._pause_for_unsupported_semantic(
                event=f"combat.attack_{category}:{line}",
                source=source,
            )
            return
        candidates = [
            candidate_by_ref[ref]
            for ref in problem.domains
            if ref in candidate_by_ref
        ]
        if not candidates:
            self.state.combat.attackers_declared = True
            self.state.combat.defending_players = [
                seat
                for seat in self.active_seats
                if seat != active
            ]
            self._grant_priority(active)
            return
        planeswalker_defenders = self._attackable_planeswalkers(active)
        battle_defenders = self._attackable_battles(active)
        permanent_defender_ids = {
            walker["id"] for walker in planeswalker_defenders
        }
        self.permissions.issue(
            kind="combat.attackers",
            role="pilot",
            actors=[active],
            allowed_actions=["attack"],
            payload_by_actor={
                active: {
                    "candidates": candidates,
                    "defenders": [
                        *[
                            seat
                            for seat in self.active_seats
                            if seat != active
                        ],
                        *[
                            walker["id"]
                            for walker in planeswalker_defenders
                        ],
                        *[
                            battle["id"]
                            for battle in battle_defenders
                            if battle["id"] not in permanent_defender_ids
                        ],
                    ],
                    "planeswalker_defenders": planeswalker_defenders,
                    "battle_defenders": battle_defenders,
                    "declaration_constraints": problem.projection(),
                    "declaration_costs": [
                        cost.to_dict() for cost in costs
                    ],
                    "payment": {
                        "default": "auto",
                        "manual_fields": ["mana", "payment"],
                        "spend_context": "combat_declaration",
                    },
                }
            },
        )

    def _attackable_planeswalkers(
        self,
        attacker: str,
    ) -> list[dict[str, Any]]:
        planeswalkers: list[dict[str, Any]] = []
        for card in self.state.cards.values():
            if (
                card.zone != "battlefield"
                or card.phased_out
                or card.controller not in self.active_seats
                or card.controller == attacker
            ):
                continue
            card_types, _, _ = self._type_parts(
                str(
                    self._effective_card_data(card).get("type_line")
                    or ""
                )
            )
            if "planeswalker" not in card_types:
                continue
            planeswalkers.append(
                {
                    "id": card.ref,
                    "name": self.display_name(card.object_id),
                    "controller": card.controller,
                    "loyalty": int(card.counters.get("loyalty", 0)),
                }
            )
        return sorted(planeswalkers, key=lambda value: value["id"])

    def _attackable_battles(self, attacker: str) -> list[dict[str, Any]]:
        battles: list[dict[str, Any]] = []
        for card in self.state.cards.values():
            if (
                card.zone != "battlefield"
                or card.phased_out
                or card.battle_protector not in self.active_seats
                or card.battle_protector == attacker
            ):
                continue
            card_types, _, _ = self._type_parts(
                str(
                    self._effective_card_data(card).get("type_line")
                    or ""
                )
            )
            if "battle" not in card_types:
                continue
            battles.append(
                {
                    "id": card.ref,
                    "name": self.display_name(card.object_id),
                    "controller": card.controller,
                    "protector": card.battle_protector,
                    "defense": int(
                        card.counters.get("defense", 0)
                    ),
                }
            )
        return sorted(battles, key=lambda value: value["id"])

    def _battle_for_attack_target(
        self,
        value: str,
    ) -> CardInstance | None:
        battle = next(
            (
                card
                for card in self.state.cards.values()
                if card.ref == value
                and card.zone == "battlefield"
                and not card.phased_out
            ),
            None,
        )
        if battle is None:
            return None
        card_types, _, _ = self._type_parts(
            str(
                self._effective_card_data(battle).get("type_line")
                or ""
            )
        )
        return battle if "battle" in card_types else None

    def _planeswalker_for_attack_target(
        self,
        value: str,
    ) -> CardInstance | None:
        planeswalker = next(
            (
                card
                for card in self.state.cards.values()
                if card.ref == value
                and card.zone == "battlefield"
                and not card.phased_out
            ),
            None,
        )
        if planeswalker is None:
            return None
        card_types, _, _ = self._type_parts(
            str(
                self._effective_card_data(planeswalker).get("type_line")
                or ""
            )
        )
        return planeswalker if "planeswalker" in card_types else None

    def _attack_target_details(
        self,
        attacker: str,
        value: str,
    ) -> dict[str, str] | None:
        if value in self.active_seats and value != attacker:
            return {
                "target": value,
                "kind": "player",
                "defending_player": value,
            }
        planeswalker = self._planeswalker_for_attack_target(value)
        if (
            planeswalker is not None
            and planeswalker.controller in self.active_seats
            and planeswalker.controller != attacker
        ):
            return {
                "target": planeswalker.ref,
                "kind": "planeswalker",
                "defending_player": planeswalker.controller,
                "logical_object_id": planeswalker.logical_object_id,
            }
        battle = self._battle_for_attack_target(value)
        if (
            battle is not None
            and battle.battle_protector in self.active_seats
            and battle.battle_protector != attacker
        ):
            return {
                "target": battle.ref,
                "kind": "battle",
                "defending_player": str(battle.battle_protector),
                "logical_object_id": battle.logical_object_id,
            }
        return None

    def _defending_player_for_attack_target(
        self,
        value: str,
    ) -> str | None:
        if value in self.active_seats:
            return value
        planeswalker = self._planeswalker_for_attack_target(value)
        if planeswalker is not None:
            return planeswalker.controller
        battle = self._battle_for_attack_target(value)
        return battle.battle_protector if battle is not None else None

    def _defending_player_for_attacker(
        self,
        attacker_id: str,
        target: str,
    ) -> str | None:
        context = self.state.combat.attack_target_context.get(attacker_id)
        if context is not None:
            defender = context.get("defending_player")
            return defender if defender in self.state.players else None
        return self._defending_player_for_attack_target(target)

    def _complete_attackers(self, decision: Any) -> None:
        active = decision.actors[0]
        response = decision.responses[active]
        declarations = response.get("attackers")
        if declarations is None:
            declarations = response.get("attacks")
        declarations = declarations or {}
        if isinstance(declarations, list):
            if all(isinstance(value, Mapping) for value in declarations):
                normalized: dict[str, Any] = {}
                for value in declarations:
                    attacker = value.get("attacker") or value.get("id")
                    defender = value.get("defender")
                    if attacker is None or defender is None:
                        raise GameRuleError(
                            "Each attack declaration needs attacker and defender"
                        )
                    attacker_ref = str(attacker)
                    if attacker_ref in normalized:
                        raise GameRuleError(
                            "A creature cannot be declared twice"
                        )
                    normalized[attacker_ref] = defender
                declarations = normalized
            else:
                default_defender = response.get("defender")
                declarations = {
                    str(value): default_defender for value in declarations
                }
        if not isinstance(declarations, Mapping):
            raise GameRuleError("Attack declarations must be a mapping or list")
        chosen: list[tuple[CardInstance, dict[str, str]]] = []
        canonical: dict[str, str] = {}
        used: set[str] = set()
        for value, defender in dict(declarations).items():
            card = self._resolve_object(active, str(value), zones={"battlefield"}, controlled_only=True)
            if card.object_id in used:
                raise GameRuleError("A creature cannot be declared twice")
            defender = str(defender)
            target_details = self._attack_target_details(active, defender)
            if target_details is None:
                raise GameRuleError(f"Invalid attack defender {defender}")
            defender = target_details["target"]
            declaration_error = self._attack_declaration_error(
                card,
                active,
            )
            if declaration_error is not None:
                raise GameRuleError(declaration_error)
            chosen.append((card, target_details))
            canonical[card.ref] = defender
            used.add(card.object_id)

        problem, locked_costs, unresolved = self._attack_declaration_components(
            active
        )
        if unresolved:
            raise GameRuleError(
                "The attack declaration has unresolved restriction or cost semantics"
            )
        self._validate_declaration_requirements(problem, canonical)
        tap_declared_attackers(self, (card for card, _details in chosen))
        requirements, selected_costs = self._selected_declaration_mana(
            locked_costs,
            canonical,
            payer=active,
        )
        spent = normalize_mana_bundle(None)
        activations: list[dict[str, Any]] = []
        if sum(requirements.values()):
            spent, activations = self._pay_for_cost(
                active,
                requirements,
                response,
                spend_context="combat_declaration",
            )
        committed = attack_transitions.commit_engine_attack_declaration(
            self, controller=active, chosen=chosen
        )
        surviving_attackers = [
            (self.state.cards[value.attacker_object_id], value.target_context)
            for value in committed
        ]
        for card, target_details in surviving_attackers:
            self._record_turn_history(
                "creature_attacked",
                actor=active,
                object_incarnation=card.logical_object_id,
                target=target_details["target"],
                target_kind=target_details["kind"],
            )
        used = {card.object_id for card, _ in surviving_attackers}
        self.state.combat.attackers_declared = True
        if used:
            self.state.combat.had_attacking_creature = True
        self.state.combat.defending_players = [
            seat
            for seat in self.active_seats
            if seat != active
        ]
        self._log(
            active,
            "combat.attack",
            f"{active} attacked with {len(used)} creature(s).",
            {
                "attackers": {
                    self.state.cards[oid].ref: defender
                    for oid, defender in self.state.combat.attackers.items()
                },
                "costs": [cost.cost_id for cost in selected_costs],
                "requirements": {
                    key: value
                    for key, value in requirements.items()
                    if value
                },
                "payment": {
                    key: value for key, value in spent.items() if value
                },
                "mana_sources": [
                    {
                        "source": activation.get("source_ref")
                        or activation.get("source"),
                        "bundle": activation.get("bundle"),
                    }
                    for activation in activations
                ],
            },
            importance=2,
            changed_objects=list(used),
            changed_players=[active],
        )
        attack_triggers: list[StackItem] = []
        attack_triggers.extend(
            attack_transitions.attack_transition_stack_items(self)
        )
        enqueue_trigger_batch(self, attack_triggers)
        self._grant_priority(active)

    def _attacked_defending_players(self) -> list[str]:
        """Return only defenders whose player or permanent is attacked."""

        attacked = {
            self._defending_player_for_attacker(attacker_id, target)
            for attacker_id, target in self.state.combat.attackers.items()
        }
        return [
            seat
            for seat in self.apnap_order()
            if seat in attacked
        ]

    def _current_attacker_cards(self) -> list[CardInstance]:
        attackers: list[CardInstance] = []
        for object_id in self.state.combat.attackers:
            card = self.state.cards.get(object_id)
            if (
                card is None
                or card.zone != "battlefield"
                or card.controller != self.state.active_player
                or card.phased_out
            ):
                continue
            card_types, _, _ = self._type_parts(
                str(
                    self._effective_card_data(card).get("type_line")
                    or ""
                )
            )
            if "creature" in card_types and "battle" not in card_types:
                attackers.append(card)
        return attackers

    def _begin_blocker_decisions(self) -> None:
        if not self._current_attacker_cards():
            self.state.combat.blockers_declared = True
            self._grant_priority(self.state.active_player)
            return
        self.state.combat.blocker_cursor = 0
        self._issue_next_blocker()

    def _issue_next_blocker(self) -> None:
        defenders = self._attacked_defending_players()
        if self.state.combat.blocker_cursor >= len(defenders):
            self.state.combat.blockers_declared = True
            block_triggers.enqueue_block_transition_triggers(self)
            self._grant_priority(self.state.active_player)
            return
        defender = defenders[self.state.combat.blocker_cursor]
        attacker_cards = [
            card
            for card in self._current_attacker_cards()
            if self._defending_player_for_attacker(
                card.object_id,
                self.state.combat.attackers[card.object_id],
            )
            == defender
        ]
        attackers = [card.ref for card in attacker_cards]
        problem, costs, unresolved, menace_restrictions = (
            self._block_declaration_components(defender)
        )
        minimum_blockers = {
            restriction.attacker_ref: restriction.minimum_blockers
            for restriction in menace_restrictions
        }
        if unresolved:
            source, line, category = unresolved[0]
            self._pause_for_unsupported_semantic(
                event=f"combat.block_{category}:{line}",
                source=source,
            )
            return
        if not problem.domains:
            self._log(
                defender,
                "combat.block",
                f"{defender} had no legal blockers.",
                {
                    "blocks": {},
                    "costs": [],
                    "requirements": {},
                    "payment": {},
                    "mana_sources": [],
                    "automatic": True,
                },
                importance=1,
                changed_players=[defender],
            )
            self.state.combat.blocker_cursor += 1
            self._issue_next_blocker()
            return
        blockers = list(problem.domains)
        legal_blocks = {
            blocker: list(options)
            for blocker, options in problem.domains.items()
        }
        self.permissions.issue(
            kind="combat.blockers",
            role="pilot",
            actors=[defender],
            allowed_actions=["block"],
            payload_by_actor={
                defender: {
                    "attackers": attackers,
                    "blockers": blockers,
                    "legal_blocks": legal_blocks,
                    "minimum_blockers": minimum_blockers,
                    "declaration_constraints": problem.projection(),
                    "declaration_costs": [
                        cost.to_dict() for cost in costs
                    ],
                    "payment": {
                        "default": "auto",
                        "manual_fields": ["mana", "payment"],
                        "spend_context": "combat_declaration",
                    },
                }
            },
        )

    def _complete_blockers(self, decision: Any) -> None:
        defender = decision.actors[0]
        response = decision.responses[defender]
        assignments = dict(response.get("blocks") or {})  # blocker ref -> attacker ref
        chosen: list[tuple[CardInstance, CardInstance]] = []
        canonical: dict[str, str] = {}
        used_blockers: set[str] = set()
        for blocker_value, attacker_value in assignments.items():
            blocker = self._resolve_object(defender, str(blocker_value), zones={"battlefield"}, controlled_only=True)
            attacker = self._resolve_object(defender, str(attacker_value), zones={"battlefield"})
            if blocker.object_id in used_blockers:
                raise GameRuleError("A blocker cannot block more than one attacker without an explicit rule")
            attack_target = self.state.combat.attackers.get(
                attacker.object_id
            )
            if (
                attack_target is None
                or self._defending_player_for_attacker(
                    attacker.object_id,
                    attack_target,
                )
                != defender
            ):
                raise GameRuleError(f"{attacker.ref} is not attacking {defender}")
            blocker_types, _, _ = self._type_parts(
                str(
                    self._effective_card_data(blocker).get("type_line")
                    or ""
                )
            )
            if (
                blocker.tapped
                or blocker.phased_out
                or "creature" not in blocker_types
            ):
                raise GameRuleError(f"{blocker.ref} cannot block")
            if "battle" in blocker_types:
                raise GameRuleError(
                    f"{blocker.ref} cannot block because it is a Battle"
                )
            can_block, reason = self._can_block(attacker, blocker)
            if not can_block:
                raise GameRuleError(
                    f"{blocker.ref} cannot block {attacker.ref}: {reason}"
                )
            chosen.append((blocker, attacker))
            canonical[blocker.ref] = attacker.ref
            used_blockers.add(blocker.object_id)

        problem, costs, unresolved, _ = self._block_declaration_components(
            defender
        )
        if unresolved:
            raise GameRuleError(
                "The block declaration has unresolved restriction or cost semantics"
            )
        self._validate_declaration_requirements(problem, canonical)
        requirements, selected_costs = self._selected_declaration_mana(
            costs,
            canonical,
            payer=defender,
        )
        spent = normalize_mana_bundle(None)
        activations: list[dict[str, Any]] = []
        if sum(requirements.values()):
            spent, activations = self._pay_for_cost(
                defender,
                requirements,
                response,
                spend_context="combat_declaration",
            )
        committed = block_triggers.commit_engine_block_declaration(
            self,
            controller=defender,
            chosen=chosen,
        )
        used_blockers = {
            assignment.blocker_object_id for assignment in committed
        }
        self._log(
            defender,
            "combat.block",
            f"{defender} declared {len(used_blockers)} blocker(s).",
            {
                "blocks": {
                    self.state.cards[b].ref: self.state.cards[a].ref
                    for a, blockers in self.state.combat.blockers.items()
                    for b in blockers
                    if b in used_blockers
                },
                "costs": [cost.cost_id for cost in selected_costs],
                "requirements": {
                    key: value
                    for key, value in requirements.items()
                    if value
                },
                "payment": {
                    key: value for key, value in spent.items() if value
                },
                "mana_sources": [
                    {
                        "source": activation.get("source_ref")
                        or activation.get("source"),
                        "bundle": activation.get("bundle"),
                    }
                    for activation in activations
                ],
            },
            importance=2,
            changed_objects=list(used_blockers),
            changed_players=[defender],
        )
        self.state.combat.blocker_cursor += 1
        self._issue_next_blocker()

    def _begin_combat_damage(self) -> None:
        self._initialize_combat_damage_steps()
        actors = [
            seat
            for seat in self.apnap_order()
            if self._combat_damage_source_options(seat)
        ]
        self._continue_combat_damage_assignments(
            CombatDamageAssignmentSequence(actors=tuple(actors))
        )

    def _continue_combat_damage_assignments(
        self,
        sequence: CombatDamageAssignmentSequence,
    ) -> None:
        """Collect CR 510.1/802.5 assignments in public APNAP order."""

        if not isinstance(sequence, CombatDamageAssignmentSequence):
            raise GameRuleError(
                "Combat damage sequencing requires typed immutable state"
            )
        ordered_actors = tuple(
            seat for seat in sequence.actors if seat in self.active_seats
        )
        if ordered_actors != sequence.actors:
            raise GameRuleError(
                "Combat damage assignment order became stale"
            )
        for announcement in sequence.announcements:
            current_proposal = project_combat_damage_assignment(
                EngineCombatDamageQuery(self), announcement.actor
            )
            if current_proposal.proposal_id != announcement.proposal_id:
                raise GameRuleError(
                    "A previously announced combat damage proposal became stale"
                )
        current = sequence
        while (seat := current.pending_actor) is not None:
            proposal = project_combat_damage_assignment(
                EngineCombatDamageQuery(self), seat
            )
            automatic = proposal.automatic_assignments()
            if automatic is not None:
                current = current.announce(
                    actor=seat,
                    proposal_id=proposal.proposal_id,
                    assignments=automatic,
                    automatic=True,
                )
                self._record_combat_damage_announcement(
                    current.announcements[-1]
                )
                continue

            self.permissions.issue(
                kind="combat.damage",
                role="pilot",
                actors=[seat],
                allowed_actions=["assign_damage"],
                payload_by_actor={
                    seat: {
                        "combat": self._combat_payload(
                            seat,
                            announced_assignments=(
                                value.to_dict()
                                for value in current.collected_assignments
                            ),
                        ),
                        "instruction": (
                            "Assign damage for sources you control. Earlier "
                            "APNAP assignments are final and public."
                        ),
                    }
                },
                continuation={
                    "combat_damage_sequence": current.to_dict(),
                },
            )
            return

        waiting = self._apply_combat_assignments(
            [value.to_dict() for value in current.collected_assignments]
        )
        if not waiting:
            self._grant_priority(self.state.active_player)

    def _record_combat_damage_announcement(
        self,
        announcement: CombatDamageAnnouncement,
    ) -> None:
        canonical = [value.to_dict() for value in announcement.assignments]
        self._log(
            announcement.actor,
            "combat.damage.assigned",
            f"{announcement.actor} announced combat-damage assignments.",
            {
                "player": announcement.actor,
                "assignments": canonical,
                "announcement_index": announcement.announcement_index,
                "automatic": announcement.automatic,
                "proposal_id": announcement.proposal_id,
                "damage_step": self.state.combat.damage_step_index + 1,
            },
            importance=1,
            changed_players=[announcement.actor],
        )

    def _complete_combat_damage(self, decision: Any) -> None:
        serialized_sequence = decision.continuation.get(
            "combat_damage_sequence"
        )
        if serialized_sequence is None:
            # Backward-compatible completion for a pending pre-v2 checkpoint.
            assignments: list[dict[str, Any]] = []
            for seat in decision.actors:
                assignments.extend(
                    self._validated_combat_damage_assignments(
                        seat,
                        decision.responses[seat].get("assignments") or [],
                    )
                )
            waiting = self._apply_combat_assignments(assignments)
            if not waiting:
                self._grant_priority(self.state.active_player)
            return
        try:
            sequence = CombatDamageAssignmentSequence.from_dict(
                serialized_sequence
            )
        except (CombatDamageSequenceError, TypeError) as exc:
            raise GameRuleError(str(exc)) from exc
        seat = sequence.pending_actor
        if seat is None:
            raise GameRuleError(
                "Completed combat damage sequence cannot receive a response"
            )
        if decision.actors != [seat]:
            raise GameRuleError(
                "Only the current APNAP player may assign combat damage"
            )
        proposal = project_combat_damage_assignment(
            EngineCombatDamageQuery(self), seat
        )
        try:
            assignments = proposal.validate(
                decision.responses[seat].get("assignments") or []
            )
            sequence = sequence.announce(
                actor=seat,
                proposal_id=proposal.proposal_id,
                assignments=assignments,
                automatic=False,
            )
        except (CombatDamageAssignmentError, CombatDamageSequenceError) as exc:
            raise GameRuleError(str(exc)) from exc
        self._record_combat_damage_announcement(
            sequence.announcements[-1]
        )
        self._continue_combat_damage_assignments(sequence)

    def _validated_combat_damage_assignments(
        self,
        seat: str,
        submitted: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Compatibility validator for pending pre-sequence Game Record v3 state."""

        proposal = project_combat_damage_assignment(
            EngineCombatDamageQuery(self), seat
        )
        try:
            assignments = proposal.validate(submitted)
        except CombatDamageAssignmentError as exc:
            raise GameRuleError(str(exc)) from exc
        return [assignment.to_dict() for assignment in assignments]

    def _combat_damage_source_options(
        self, seat: str
    ) -> dict[str, dict[str, Any]]:
        return project_combat_damage_assignment(
            EngineCombatDamageQuery(self), seat
        ).projected_options()

    def _combat_damage_target_exists(
        self,
        target: str,
        *,
        attacker_id: str | None = None,
    ) -> bool:
        """Return whether an attack target is still a legal damage recipient.

        An attacked permanent leaving combat does not remove its attackers
        from combat (CR 506.4c), but an ordinary unblocked attacker then has
        no combat-damage recipient (CR 510.1b).  The declaration-time target
        kind and defending player keep that distinction authoritative.
        """

        if attacker_id is None:
            if target in self.state.players:
                return target in self.active_seats
            return any(
                card.ref == target
                and card.zone == "battlefield"
                and not card.phased_out
                for card in self.state.cards.values()
            )
        context = self.state.combat.attack_target_context.get(attacker_id)
        if context is None:
            context = self._attack_target_details(
                self.state.active_player, target
            )
        if context is None or context.get("target") != target:
            return False
        kind = context.get("kind")
        defender = context.get("defending_player")
        if kind == "player":
            return target == defender and target in self.active_seats
        card = next(
            (
                candidate
                for candidate in self.state.cards.values()
                if candidate.ref == target
                and candidate.zone == "battlefield"
                and not candidate.phased_out
                and (
                    context.get("logical_object_id") is None
                    or candidate.logical_object_id
                    == context["logical_object_id"]
                )
            ),
            None,
        )
        if card is None or defender not in self.active_seats:
            return False
        card_types, _, _ = self._type_parts(
            str(self._effective_card_data(card).get("type_line") or "")
        )
        if kind == "planeswalker":
            return (
                "planeswalker" in card_types
                and card.controller == defender
            )
        if kind == "battle":
            return (
                "battle" in card_types
                and card.battle_protector == defender
            )
        return False

    def _combat_payload(
        self,
        seat: str | None = None,
        *,
        announced_assignments: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        payload = {
            "attackers": {self.state.cards[oid].ref: target for oid, target in self.state.combat.attackers.items()},
            "blockers": {self.state.cards[aid].ref: [self.state.cards[bid].ref for bid in bids] for aid, bids in self.state.combat.blockers.items()},
            "damage_step": self.state.combat.damage_step_index + 1,
            "first_strike_step": self.state.combat.first_strike_step,
            "announced_assignments": [
                dict(value) for value in announced_assignments
            ],
        }
        if seat is not None:
            payload["damage_sources"] = self._combat_damage_source_options(
                seat
            )
        return payload

    def _apply_combat_assignments(
        self,
        assignments: Sequence[Mapping[str, Any]],
        *,
        replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
        replacement_event_ids: Sequence[str] = (),
    ) -> bool:
        """Deal one simultaneous combat-damage batch and stabilize it.

        Returns ``True`` when replacement ordering, trigger ordering, another
        rules choice, a semantic stop, or game end prevents the ordinary
        priority grant.
        """

        declared = [dict(value) for value in assignments]
        try:
            proposals = combat_damage_proposals(
                self,
                declared,
                damage_step_id=EngineCombatDamageQuery(self).damage_step_identity(),
                replacement_event_ids=replacement_event_ids,
            )
            result = resolve_damage_batch(
                self,
                proposals,
                replacement_selections=replacement_selections,
            )
        except ReplacementChoiceRequired as required:
            issue_combat_damage_replacement_choice(
                self,
                assignments=declared,
                selections=replacement_selections,
                required=required,
            )
            return True
        except DamageError as exc:
            raise GameRuleError(str(exc)) from exc

        self.state.combat.damage_assignments.extend(declared)
        dealt_assignments = [
            {
                "source": event.source,
                "target": event.target,
                "amount": event.dealt_amount,
            }
            for event in result.events
            if event.was_dealt
        ]
        for event in result.events:
            if not event.prevented_amount:
                continue
            self._log(
                event.target_controller,
                "combat.damage.prevented",
                (
                    f"{event.prevented_amount} damage from {event.source} "
                    f"to {event.target} was prevented."
                ),
                {
                    "source": event.source,
                    "target": event.target,
                    "assigned_amount": event.assigned_amount,
                    "dealt_amount": event.dealt_amount,
                    "prevented_amount": event.prevented_amount,
                    "applied_effects": list(event.applied_effects),
                },
                importance=1,
                changed_objects=(
                    [event.target_object_id]
                    if event.target_object_id is not None
                    else []
                ),
                changed_players=(
                    [event.target]
                    if event.target_kind == "player"
                    else []
                ),
            )
        self._log(
            None,
            "combat.damage",
            (
                "Combat damage was dealt."
                if dealt_assignments
                else "No combat damage was dealt."
            ),
            {
                "assignments": dealt_assignments,
                "declared_assignments": declared,
                "damage_step": self.state.combat.damage_step_index + 1,
                "first_strike_step": self.state.combat.first_strike_step,
                "damage_events": [
                    event.semantic_context() for event in result.events
                ],
            },
            importance=2,
            changed_objects=result.changed_objects,
            changed_players=result.changed_players,
        )
        if self._semantic_pause_annotation() is not None:
            return True
        return self._stabilize()

    # Cleanup, state-based actions, and player elimination
    # ------------------------------------------------------------------
    def _complete_cleanup_discard(self, decision: Any) -> None:
        seat = decision.actors[0]
        player = self.state.players[seat]
        values = list(decision.responses[seat].get("cards") or [])
        required = max(0, len(player.zones["hand"]) - player.max_hand_size)
        if len(values) != required:
            raise GameRuleError(f"{seat} must discard exactly {required} card(s)")
        objects: list[str] = []
        for value in values:
            card = self._resolve_object(seat, str(value), zones={"hand"}, owned_only=True)
            if card.object_id in objects:
                raise GameRuleError("Duplicate discard")
            objects.append(card.object_id)
        self._move_cards_simultaneously(
            [(object_id, "graveyard") for object_id in objects],
            reason="cleanup discard",
            log=False,
        )
        self._log(seat, "cleanup.discard", f"{seat} discarded {len(objects)} card(s) to maximum hand size.", {"objects": [self.state.cards[oid].ref for oid in objects]}, importance=1, changed_objects=objects, changed_players=[seat])
        self._finish_cleanup()

    def _numeric_stat(self, object_id: str, stat: str) -> int:
        card = self.state.cards[object_id]
        data = self._effective_card_data(card)
        value = exact_numeric_characteristic(card, data, stat)
        return value if value is not None else 0

    def _attachment_is_legal(
        self,
        attachment: CardInstance,
        *,
        subtypes: set[str],
    ) -> bool | None:
        if attachment.attached_to is None:
            return False
        if "aura" in subtypes:
            return simple_aura_attachment_is_legal(self, attachment)
        target = self.state.cards.get(attachment.attached_to)
        if target is None or target.zone == "outside":
            return False

        schema: dict[str, Any] | None
        if "equipment" in subtypes:
            schema = {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "creature": True,
                "count": 1,
            }
        elif "fortification" in subtypes:
            schema = {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "land": True,
                "count": 1,
            }
        else:
            return target.zone == "battlefield"
        if schema is None:
            return None
        try:
            group = TargetGroup.from_mapping(schema)
        except ValueError:
            return None
        row = next(
            (
                candidate
                for candidate in self._target_candidate_rows(
                    attachment.controller,
                    group,
                )
                if str(candidate["ref"]) == target.ref
            ),
            None,
        )
        if row is None or not self._target_row_matches(
            attachment.controller,
            group,
            row,
            source_ref=attachment.ref,
            as_target=False,
        ):
            return False

        if protection_verdict_for_ref(
            self,
            self._effective_card_data(target),
            attachment.ref,
        ) is not ProtectionVerdict.ALLOWED:
            return False
        return True

    def _has_world_supertype(self, card: CardInstance) -> bool:
        _, _, supertypes = self._type_parts(
            str(
                self._effective_card_data(card).get("type_line")
                or ""
            )
        )
        return "world" in supertypes

    def _refresh_world_supertype_timestamp(
        self,
        card: CardInstance,
        *,
        gained_at: int | None = None,
    ) -> bool:
        """Synchronize how long one battlefield object has been World."""

        if card.zone != "battlefield":
            card.world_supertype_timestamp = None
            return False
        if not self._has_world_supertype(card):
            card.world_supertype_timestamp = None
            return False
        if card.world_supertype_timestamp is None:
            card.world_supertype_timestamp = (
                int(gained_at)
                if gained_at is not None
                else self._next_zone_timestamp()
            )
            return True
        return False

    def _synchronize_world_supertype_timestamps(self) -> None:
        """Observe simultaneous gains/losses of the World supertype."""

        newly_world: list[CardInstance] = []
        for card in self.state.cards.values():
            if card.zone != "battlefield":
                if card.world_supertype_timestamp is not None:
                    card.world_supertype_timestamp = None
                continue
            if self._has_world_supertype(card):
                if card.world_supertype_timestamp is None:
                    newly_world.append(card)
            else:
                card.world_supertype_timestamp = None
        if newly_world:
            timestamp = self._next_zone_timestamp()
            for card in newly_world:
                card.world_supertype_timestamp = timestamp

    def _permanent_sba_snapshots(
        self,
    ) -> list[PermanentSnapshot]:
        snapshots: list[PermanentSnapshot] = []
        seen: set[str] = set()
        for seat in self.active_seats:
            for object_id in self.state.players[seat].zones["battlefield"]:
                if object_id in seen:
                    continue
                seen.add(object_id)
                card = self.state.cards[object_id]
                if card.zone != "battlefield":
                    continue
                if card.phased_out:
                    snapshots.append(
                        PermanentSnapshot(
                            object_id=card.object_id,
                            deathtouch_damage=card.deathtouch_damage,
                            phased_out=True,
                        )
                    )
                    continue
                data = self._effective_card_data(card)
                card_types, subtypes, supertypes = self._type_parts(
                    str(data.get("type_line") or "")
                )
                keywords = normalized_characteristic_keywords(data)
                snapshots.append(
                    PermanentSnapshot(
                        object_id=card.object_id,
                        card_types=frozenset(card_types),
                        subtypes=frozenset(subtypes),
                        world="world" in supertypes,
                        world_timestamp=(
                            card.world_supertype_timestamp
                        ),
                        toughness=(
                            self._numeric_stat(object_id, "toughness")
                            if "creature" in card_types
                            else None
                        ),
                        marked_damage=card.marked_damage,
                        deathtouch_damage=card.deathtouch_damage,
                        indestructible="indestructible" in keywords,
                        loyalty=(
                            int(card.counters.get("loyalty", 0))
                            if (
                                "planeswalker" in card_types
                                and (
                                    "loyalty" in card.counters
                                    or card.annotations.get(
                                        "loyalty_initialized"
                                    )
                                    or card.counters.get(
                                        "loyalty_initialized"
                                    )
                                )
                            )
                            else None
                        ),
                        defense=(
                            int(card.counters.get("defense", 0))
                            if "battle" in card_types
                            else None
                        ),
                        battle_trigger_pending=(
                            self._battle_trigger_pending(card)
                            if "battle" in card_types
                            else False
                        ),
                        saga=saga_final_chapter_snapshot(self, card),
                        attached_to=card.attached_to,
                        attachment_legal=(
                            self._attachment_is_legal(
                                card,
                                subtypes=subtypes,
                            )
                            if card.attached_to is not None
                            else False
                        ),
                        counters=dict(card.counters),
                        counter_maximums=counter_maximum_values(
                            data.get("ability_fragments", ())
                        ),
                    )
                )
        return snapshots

    def _object_sba_snapshots(self) -> list[ObjectSnapshot]:
        return [
            ObjectSnapshot(
                object_id=card.object_id,
                zone=card.zone,
                is_token=card.is_token,
                is_spell_copy=card.is_spell_copy,
                is_card_copy=card.is_card_copy,
            )
            for card in self.state.cards.values()
            if card.zone != "outside"
        ]

    def _detach_permanent(self, card: CardInstance) -> None:
        detach_object(
            self.state.cards,
            card,
            players=self.state.players,
        )

    def _legend_groups(self) -> list[tuple[str, str, list[str]]]:
        groups: dict[tuple[str, str], list[str]] = {}
        for seat in self.active_seats:
            for object_id in self.state.players[seat].zones["battlefield"]:
                card = self.state.cards[object_id]
                if card.controller != seat:
                    continue
                data = self._effective_card_data(card)
                type_line = str(data.get("type_line") or "")
                if "legendary" not in type_line.casefold():
                    continue
                key = (seat, str(data.get("name") or card.printed_name))
                groups.setdefault(key, []).append(object_id)
        return [(seat, name, ids) for (seat, name), ids in groups.items() if len(ids) > 1]

    def _repair_battle_protectors(self) -> str | None:
        """Apply or request the represented CR 704.5x-y protector repair."""

        attacked_targets = set(self.state.combat.attackers.values())
        for seat in self.active_seats:
            for object_id in list(
                self.state.players[seat].zones["battlefield"]
            ):
                battle = self.state.cards[object_id]
                if battle.zone != "battlefield" or battle.phased_out:
                    continue
                card_types, subtypes, _ = self._type_parts(
                    str(
                        self._effective_card_data(battle).get(
                            "type_line"
                        )
                        or ""
                    )
                )
                if "battle" not in card_types:
                    continue
                if not subtypes:
                    if battle.battle_protector != battle.controller:
                        battle.battle_protector = battle.controller
                        self._log(
                            battle.controller,
                            "state.battle_protector",
                            (
                                f"{battle.controller} became protector "
                                f"of {battle.ref}."
                            ),
                            {
                                "battle": battle.ref,
                                "protector": battle.controller,
                                "reason": "Battle has no Battle type",
                            },
                            importance=2,
                            changed_objects=[battle.object_id],
                            changed_players=[battle.controller],
                        )
                        return "changed"
                    continue
                if "siege" not in subtypes:
                    raise GameRuleError(
                        "The protector predicate for Battle type(s) "
                        f"{sorted(subtypes)} is not compiled"
                    )
                protector_valid = (
                    battle.battle_protector in self.active_seats
                    and battle.battle_protector != battle.controller
                )
                if protector_valid:
                    continue
                if (
                    battle.battle_protector != battle.controller
                    and battle.ref in attacked_targets
                ):
                    # CR 704.5x waits until no creature is attacking this
                    # Battle. CR 704.5y has no such exception when a Siege's
                    # controller is also its protector.
                    continue
                candidates = [
                    opponent
                    for opponent in self.active_seats
                    if opponent != battle.controller
                ]
                if not candidates:
                    self._move_cards_simultaneously(
                        [(battle.object_id, "graveyard")],
                        reason="no legal Battle protector",
                        log=False,
                    )
                    self._log(
                        battle.controller,
                        "state.battle_protector",
                        (
                            f"{battle.ref} had no legal protector and "
                            "went to its owner's graveyard."
                        ),
                        {
                            "battle": battle.ref,
                            "protector": None,
                            "reason": "no_legal_protector",
                        },
                        importance=2,
                        changed_objects=[battle.object_id],
                    )
                    return "changed"
                self._begin_battle_protector_repair_choice(
                    battle,
                    candidates,
                )
                return "waiting"
        return None

    def _stabilize(self) -> bool:
        """Perform state-based actions until stable.

        Returns True when an external choice (currently the legend rule) or game
        end prevents priority from being granted.
        """
        for _ in range(100):
            if self.state.game_over:
                return True
            losers = player_loss_seats(self.state, self.active_seats)
            if losers:
                self._eliminate_players(losers, reason="state-based loss")
                if self.state.game_over:
                    return True
                continue

            if self._remove_invalid_combat_objects():
                continue

            try:
                commander_zone_choices = pending_commander_zone_state_choices(
                    self.state.cards.values(),
                    active_seats=self.active_seats,
                    apnap_order=self.apnap_order(),
                )
            except CommanderZoneError as exc:
                raise GameRuleError(str(exc)) from exc
            if commander_zone_choices:
                self._begin_commander_zone_choice(
                    commander_zone_choices[0]
                )
                return True

            self._synchronize_world_supertype_timestamps()
            sba_batch = evaluate_state_based_actions(
                permanents=self._permanent_sba_snapshots(),
                objects=self._object_sba_snapshots(),
            )
            execution = prepare_state_based_execution(self, sba_batch)
            consume_deathtouch_damage_checks(
                self, sba_batch.deathtouch_checks
            )
            if execution.state_changed:
                world_rule_rows = [
                    {
                        "object": self.state.cards[object_id].ref,
                    }
                    for object_id in sba_batch.world_rule
                    if self.state.cards[object_id].zone
                    == "battlefield"
                ]
                world_rule_ids = set(sba_batch.world_rule)
                world_survivors = [
                    card.ref
                    for card in self.state.cards.values()
                    if card.zone == "battlefield"
                    and not card.phased_out
                    and card.world_supertype_timestamp is not None
                    and card.object_id not in world_rule_ids
                ]
                commit_state_based_zone_changes(self, execution)
                detached: list[str] = []
                for object_id in sba_batch.detach:
                    card = self.state.cards[object_id]
                    if (
                        card.zone == "battlefield"
                        and card.attached_to is not None
                    ):
                        self._detach_permanent(card)
                        detached.append(object_id)
                counter_result = commit_state_based_counter_removals(
                    self, execution.counter_removals
                )
                counter_changes = [
                    {
                        "object": self.state.cards[value.object_id].ref,
                        "pairs_removed": value.pairs_removed,
                    }
                    for value in counter_result.pairs
                ]
                maximum_counter_changes = [
                    {
                        "object": self.state.cards[value.object_id].ref,
                        "counter": value.counter_name,
                        "before": value.before,
                        "maximum": value.maximum,
                        "required_removal": value.required_removal,
                        "after": value.after,
                    }
                    for value in counter_result.maximums
                ]
                ceased: list[dict[str, Any]] = []
                ceased_object_ids: list[str] = []
                for object_id in sba_batch.cease:
                    card = self.state.cards[object_id]
                    if card.zone == "outside":
                        continue
                    previous_zone = card.zone
                    if previous_zone == "stack":
                        self.state.stack = [
                            item
                            for item in self.state.stack
                            if item.card_object_id != card.object_id
                        ]
                    else:
                        self._remove_from_zone(card)
                    card.zone = "outside"
                    card.known_to = list(self.seats)
                    card.revealed_to = list(self.seats)
                    ceased.append(
                        {
                            "object": card.ref,
                            "kind": (
                                "token"
                                if card.is_token
                                else card.object_kind
                            ),
                            "zone": previous_zone,
                        }
                    )
                    ceased_object_ids.append(card.object_id)
                if execution.ordinary_move_to_grave:
                    self._log(
                        None,
                        "state.creatures_died",
                        (
                            "State-based actions moved "
                            f"{len(execution.ordinary_move_to_grave)} permanent(s) "
                            "to graveyards."
                        ),
                        {
                            "objects": [
                                self.state.cards[object_id].ref
                                for object_id in (
                                    execution.ordinary_move_to_grave
                                )
                            ],
                            "put_in_graveyard": [
                                self.state.cards[object_id].ref
                                for object_id in (
                                    sba_batch.put_in_graveyard
                                )
                            ],
                            "destroyed": [
                                self.state.cards[object_id].ref
                                for object_id in (
                                    execution.destruction.destroyed_object_ids
                                )
                            ],
                        },
                        importance=2,
                        changed_objects=execution.ordinary_move_to_grave,
                    )
                if world_rule_rows:
                    self._log(
                        None,
                        "state.world_rule",
                        (
                            "The world rule moved "
                            f"{len(world_rule_rows)} permanent(s) to "
                            "their owners' graveyards."
                        ),
                        {
                            "moved": world_rule_rows,
                            "survivors": world_survivors,
                        },
                        importance=2,
                        changed_objects=list(sba_batch.world_rule),
                        changed_players=sorted(
                            {
                                self.state.cards[object_id].owner
                                for object_id in (
                                    sba_batch.world_rule
                                )
                            }
                        ),
                    )
                if detached:
                    self._log(
                        None,
                        "state.attachments_unattached",
                        (
                            "State-based actions unattached "
                            f"{len(detached)} permanent(s)."
                        ),
                        {
                            "objects": [
                                self.state.cards[object_id].ref
                                for object_id in detached
                            ]
                        },
                        importance=2,
                        changed_objects=detached,
                    )
                if counter_changes:
                    self._log(
                        None,
                        "state.counters_annihilated",
                        (
                            "State-based actions removed opposing "
                            "+1/+1 and -1/-1 counters."
                        ),
                        {"changes": counter_changes},
                        importance=2,
                        changed_objects=[
                            object_id
                            for object_id, _ in (
                                sba_batch.counter_pairs_to_remove
                            )
                            if self.state.cards[object_id].zone
                            == "battlefield"
                        ],
                    )
                if maximum_counter_changes:
                    self._log(
                        None,
                        "state.counter_maximums",
                        (
                            "State-based actions enforced "
                            "maximum-counter abilities."
                        ),
                        {"changes": maximum_counter_changes},
                        importance=2,
                        changed_objects=[
                            object_id
                            for object_id, _, _ in (
                                sba_batch.counter_maximums_to_remove
                            )
                            if self.state.cards[object_id].zone
                            == "battlefield"
                        ],
                    )
                if ceased:
                    self._log(
                        None,
                        "state.objects_ceased",
                        (
                            "State-based actions caused "
                            f"{len(ceased)} token or copy object(s) to "
                            "cease to exist."
                        ),
                        {"objects": ceased},
                        importance=2,
                        changed_objects=ceased_object_ids,
                        changed_players=sorted(
                            {
                                self.state.cards[object_id].owner
                                for object_id in ceased_object_ids
                            }
                        ),
                    )
                continue

            protector_repair = self._repair_battle_protectors()
            if protector_repair == "waiting":
                return True
            if protector_repair == "changed":
                continue

            legends = self._legend_groups()
            if legends:
                seat, name, ids = legends[0]
                self._begin_legend_choice(seat, name, ids)
                return True
            if begin_pending_trigger_batch(self):
                return True
            if begin_trigger_target_selection(self):
                return True
            return False
        raise StateInvariantError("State-based action loop did not stabilize")

    def _eliminate_players(self, seats: Sequence[str], *, reason: str) -> None:
        unique = [seat for seat in unique_preserving_order(seats) if seat in self.active_seats]
        if not unique:
            return
        departing_monarch = self.state.monarch in unique
        monarch_before_departure = self.state.monarch
        active_before_departure = self.state.active_player
        for seat in unique:
            player = self.state.players[seat]
            player.in_game = False
            self.state.eliminated_players.append(seat)
            # Objects owned by the player leave the game.
            # Checkpoints are serialized with sorted mapping keys, while a
            # continuously running game retains construction order. Zone
            # timestamps are authoritative, so elimination must not allocate
            # them according to incidental dictionary insertion order.
            for card in sorted(
                self.state.cards.values(),
                key=lambda value: (value.ref, value.object_id),
            ):
                if card.owner == seat and card.zone != "outside":
                    hidden_identity = card.zone in HIDDEN_ZONES or card.face_down
                    if card.zone == "stack":
                        self.state.stack = [item for item in self.state.stack if item.card_object_id != card.object_id]
                        card.zone = "outside"
                    elif hidden_identity:
                        # A player leaving is not a reveal instruction. Preserve
                        # object identity authoritatively while retaining only
                        # knowledge that existed before the player left.
                        self._remove_from_zone(card)
                        self._reset_zone_change(card, "outside")
                        card.zone = "outside"
                        card.annotations["hidden_after_owner_left"] = True
                        card.known_to = sorted(set(card.known_to).union({card.owner}))
                        card.revealed_to = [
                            viewer
                            for viewer in card.revealed_to
                            if viewer in card.known_to
                        ]
                    else:
                        self.move_card(card.object_id, "outside", reason="owner left game", log=False)
            # A conservative baseline for ended control effects: surviving
            # objects owned by others return to their owners; any leftovers are
            # exiled. A compiled continuous-effect layer may refine this later.
            for card in sorted(
                self.state.cards.values(),
                key=lambda value: (value.ref, value.object_id),
            ):
                if card.zone == "battlefield" and card.controller == seat and card.owner != seat:
                    owner = card.owner
                    if self.state.players[owner].in_game:
                        self.change_control(card.object_id, owner, reason="controller left game")
                    else:
                        self.move_card(card.object_id, "exile", reason="controller left game", log=False)
            self.state.stack = [item for item in self.state.stack if item.controller != seat or item.card_object_id is not None]
            self.state.extra_turns = [turn for turn in self.state.extra_turns if turn.player != seat]
            self.state.priority_passes = [passed for passed in self.state.priority_passes if passed != seat]
            self._log(seat, "player.eliminated", f"{seat} left the game: {reason}.", {"reason": reason}, importance=3, changed_players=[seat])

        remaining = self.active_seats
        if departing_monarch:
            if not remaining:
                previous = self.state.monarch
                self.state.monarch = None
                self._log(
                    None,
                    "monarch.change",
                    "No player is the monarch.",
                    {
                        "player": None,
                        "previous": previous,
                        "reason": "the monarch left the game",
                    },
                    importance=2,
                    changed_players=(
                        [str(previous)] if previous is not None else []
                    ),
                )
            else:
                successor = (
                    active_before_departure
                    if active_before_departure in remaining
                    else self._next_active_after(
                        str(
                            active_before_departure
                            or monarch_before_departure
                            or self.state.turn_order[-1]
                        )
                    )
                )
                self.become_monarch(
                    str(successor),
                    reason="the monarch left the game",
                )
        self.state.combat.defending_players = [
            seat
            for seat in self.state.combat.defending_players
            if seat in remaining
        ]
        if len(remaining) == 1:
            self.state.game_over = True
            self.state.winner = remaining[0]
            self.state.priority_player = None
            self.permissions.invalidate_current()
            self._log(remaining[0], "game.win", f"{remaining[0]} won the game.", importance=3, changed_players=remaining)
        elif not remaining:
            self.state.game_over = True
            self.state.draw = True
            self.state.priority_player = None
            self.permissions.invalidate_current()
            self._log(None, "game.draw", "All remaining players lost simultaneously.", importance=3)
        elif self.state.priority_player in unique:
            self.state.priority_player = self._next_active_after(unique[-1])

    # ------------------------------------------------------------------
    # Generic effect DSL used only by the arbiter/semantic executor
    # ------------------------------------------------------------------
    def apply_effect(
        self,
        effect: Mapping[str, Any],
        *,
        actor: str,
        as_cost: bool = False,
    ) -> Any:
        effect = normalize_game_record_v3_effect(effect)
        op = str(effect.get("op") or "").casefold()
        reason = str(effect.get("reason") or ("cost" if as_cost else "effect"))
        try:
            typed_plan = default_semantic_interpreter().lower_for_seats(
                effect,
                actor=actor,
                default_reason=reason,
                seats=self.seats,
                active_seats=self.active_seats,
                apnap_order=self.apnap_order(),
            )
        except SemanticNodeError as exc:
            raise GameRuleError(str(exc)) from exc
        if typed_plan is None:
            raise GameRuleError(f"Unsupported effect operation {op!r}")
        draw_batch = draw_resolution_batch(typed_plan)
        if draw_batch is not None:
            before = {
                seat: tuple(self.state.players[seat].zones["hand"])
                for seat in {
                    intent.player for intent in draw_batch.intents
                }
            }
            try:
                begin_draw_batch(
                    self,
                    tuple(
                        QueuedDraw(
                            player=intent.player,
                            count=intent.count,
                            reason=intent.reason,
                            private=intent.private,
                            post_draw_actions=intent.post_draw_actions,
                        )
                        for intent in draw_batch.intents
                    ),
                )
            except DrawError as exc:
                raise GameRuleError(str(exc)) from exc
            results = [
                (
                    intent.player,
                    [
                        object_id
                        for object_id in self.state.players[
                            intent.player
                        ].zones["hand"]
                        if object_id not in before[intent.player]
                    ],
                )
                for intent in draw_batch.intents
            ]
            if typed_plan.result_shape == "by_player":
                return dict(results)
            return results[0][1] if results else []
        return execute_intent_plan(self, typed_plan)

    def create_emblem(
        self,
        owner: str,
        *,
        abilities: Sequence[str],
        display_label: str = "Emblem",
        semantic_key: str | None = None,
        reason: str = "emblem effect",
    ) -> str:
        """Create a public noncard, nonpermanent command-zone object."""

        self._require_seat(owner, in_game=True)
        normalized_abilities = [
            str(ability).strip() for ability in abilities
        ]
        if (
            not normalized_abilities
            or any(not ability for ability in normalized_abilities)
        ):
            raise GameRuleError(
                "An emblem must have at least one nonempty ability"
            )
        ref = self._next_ref("E")
        object_id = self._stable_runtime_id("emblem-object", ref)
        emblem = CardInstance(
            object_id=object_id,
            ref=ref,
            oracle_id=(
                "custom-emblem:"
                + self._stable_runtime_id("emblem-oracle", ref)
            ),
            printed_name=str(display_label or "Emblem"),
            owner=owner,
            controller=owner,
            zone="command",
            object_kind="emblem",
            zone_timestamp=self._next_zone_timestamp(),
            annotations={
                "display_label": str(display_label or "Emblem"),
                "emblem_abilities": normalized_abilities,
                "emblem_semantic_key": semantic_key,
                "object_characteristics": {
                    "type_line": "",
                    "oracle_text": "\n".join(normalized_abilities),
                    "colors": [],
                    "keywords": [],
                },
            },
            known_to=list(self.seats),
            revealed_to=list(self.seats),
        )
        self.state.cards[object_id] = emblem
        self.state.players[owner].zones["command"].append(object_id)
        self.state.players[owner].stats["emblem_objects_v1"] = True
        self._log(
            owner,
            "emblem.create",
            f"{owner} created {emblem.printed_name}.",
            {
                "object": ref,
                "label": emblem.printed_name,
                "abilities": list(normalized_abilities),
                "semantic_key": semantic_key,
                "reason": reason,
            },
            importance=3,
            changed_objects=[object_id],
            changed_players=[owner],
        )
        return ref

    def create_token(
        self,
        controller: str,
        *,
        name: str,
        quantity: int = 1,
        tapped: bool = False,
        attacking: str | None = None,
        battle_protector: str | None = None,
        copy_of: str | None = None,
        characteristics: Mapping[str, Any] | None = None,
        temporary_keywords: Sequence[str] = (),
        aura_target_ref: str | None = None,
        reason: str = "token effect",
        replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
    ) -> list[str]:
        try:
            return create_tokens(
                self,
                controller,
                name=name,
                quantity=quantity,
                tapped=tapped,
                attacking=attacking,
                battle_protector=battle_protector,
                copy_of=copy_of,
                characteristics=characteristics,
                temporary_keywords=temporary_keywords,
                aura_target_ref=aura_target_ref,
                reason=reason,
                replacement_selections=replacement_selections,
            )
        except TokenCreationError as exc:
            raise GameRuleError(str(exc)) from exc

    def change_control(self, object_id: str, new_controller: str, *, reason: str = "") -> None:
        self._require_seat(new_controller, in_game=True)
        card = self.state.cards[object_id]
        if card.zone != "battlefield":
            raise GameRuleError("Only battlefield permanents have controllers")
        old = card.controller
        self._remove_object_from_combat(
            card,
            reason="control changed",
        )
        self.state.players[old].zones["battlefield"].remove(object_id)
        self.state.players[new_controller].zones["battlefield"].append(object_id)
        card.controller = new_controller
        control_history.record_control_change(self.state, card, self._next_zone_timestamp)
        self._log(None, "control.change", f"Control of {card.ref} changed {old} → {new_controller}.", {"object": card.ref, "from": old, "to": new_controller, "reason": reason}, importance=2, changed_objects=[object_id], changed_players=[old, new_controller])

    def apply_shortcut(
        self,
        seat: str,
        proposal: Mapping[str, Any],
    ) -> dict[str, Any]:
        from .card_overrides.shortcuts import execute_shortcut

        self._require_seat(seat, in_game=True)
        return execute_shortcut(self, seat, proposal)

    # ------------------------------------------------------------------
    # Safe testing helper
    # ------------------------------------------------------------------
    def advance_until(self, phase: str, step: str, *, max_transitions: int = 100) -> None:
        target = (phase, step)
        if target not in TURN_STEPS:
            raise ValueError(f"Unknown turn step {target}; valid values are {TURN_STEPS}")
        for _ in range(max_transitions):
            if (self.state.phase, self.state.step) == target:
                return
            if self.state.pending_decision is not None:
                raise GameRuleError(f"Cannot auto-advance through pending {self.state.pending_decision.kind}")
            if self.state.priority_player is not None:
                raise GameRuleError("Cannot auto-pass live priority; submit explicit pass/yield decisions")
            self._advance_step()
        raise GameRuleError(f"Did not reach {target} within {max_transitions} transitions")
