from __future__ import annotations
from ..rules.committed_quantity_followups import CommittedQuantityFollowup
from .counter_removal_handlers import execute_counter_removal
from ..rules.announced_x_player_rounds import is_zero_announced_x_player_round
from ..rules.counter_scaled_annihilator import bind_counter_scaled_annihilator
from ..rules.upkeep_source_counter_sacrifice import bind_upkeep_source_counter_sacrifice
from ..rules.nonbasic_land_source_untap import NonbasicLandSourceUntap
from ..rules.target_controller_library_reveal import (
    TargetControllerLibraryReveal, committed_target_controller_library_result, target_controller_library_facts,
)
from ..rules.qualified_token_creators import bind_qualified_token_creators
from ..bounded_hand_cast_model import ResolveBoundedHandCastIntent
from ..rules.ripple_resolution_model import ResolveRippleCastIntent,RippleBottomIntent
from .composition_intent import ResumeCompositionOrderIntent
from .graveyard_card_result_intent import GraveyardCardCounterLifeIntent
from .graveyard_exile_branch_intent import GraveyardExileBranchIntent
from .graveyard_exile_token_intent import GraveyardExileTokenIntent
from .graveyard_exile_type_outcome_intent import GraveyardExileTypeOutcomeIntent
from .mutate_intent import SelectMutateMergeIntent
from ..city_blessing_model import AscendIntent
from ..cipher_model import EncodeCipherIntent
from .soulbond_intent import PairSoulbondIntent
from ..discover_model import DiscoverCastIntent,DiscoverCleanupIntent
from ..fixed_top_exile import ResolvingExileLandPlayIntent
from ..rules.shared_type_library_play_model import ResolvingSharedTypeLibraryPlayIntent
from ..drawing.captured_hand import RevealCapturedDrawsIntent, ResolveCapturedDrawCastIntent
from ..dungeon_progress import VentureProgressIntent
from ..initiative_model import TakeInitiativeIntent
from ..targeted_graveyard_cast_model import GraveyardCastIntent
from ..rules.graveyard_copy_cast_model import ExileGraveyardCopiesIntent
from .life_batch_intent import LifeLossBatchIntent
from .life_gain_cost_intent import LifeGainCostBatchIntent
from .counter_movement_intent import COUNTER_MOVEMENT_INTENT_TYPES
from .exploit_intent import ExploitSacrificeIntent
from .life_execution import LIFE_INTENT_TYPES,execute_life_intent
from .control_intents import (
    GainControlIntent, GainControlSetIntent, GainSpellControlIntent,
    ExchangePermanentControlIntent, GiveControlIntent, GiveControlSetIntent, RestoreOwnerControlIntent,
)
from ..whole_hand_discard_model import DiscardWholeHandsIntent
from ..discard_result_model import DiscardWholeHandsResultIntent
from ..hand_library_redraw_model import HandLibraryRedrawIntent
from .colored_hand_reveal import RevealColoredHandCardsIntent
from ..rules.discard_results import execute_discard_result
from .whole_hand_discard import execute_whole_hand_discard
from ..random_discard import RandomDiscardIntent
from .random_discard import execute_random_discard
from ..rules.random_graveyard_returns import RandomGraveyardReturnIntent
from .graveyard_recycling import GraveyardRecyclingIntent
from ..target_land_type_model import TargetLandTypeSettingIntent
from ..source_sacrifice_model import FixedSourceSacrificeIntent
from .random_graveyard_returns import execute_random_graveyard_return
from ..temporary_cast_rule_model import TemporaryCastRestrictionIntent
from ..draw_redirect_model import TemporaryDrawRedirectIntent
from ..target_combat_damage_multiplier_model import TargetCombatDamageMultiplierIntent
from ..next_draw_replacement_model import ScheduleNextDrawResultIntent
from ..game_hand_size_model import NoMaximumForGameIntent
from ..protected_attack_payment_model import TemporaryProtectedAttackPaymentIntent
from ..next_turn_attack_restriction_model import NextTurnAttackRestrictionIntent
from ..extra_combat_model import ScheduleExtraCombatIntent
from ..additional_beginning_model import ScheduleAdditionalBeginningIntent
from ..rules.token_mill_repeat import TokenMillRepeatIntent, execute_token_mill_repeat
from ..rules.library_bottom_results import LibraryBottomResultIntent, commit_library_bottom_creature_result
from ..turn_skip_model import ScheduleTurnSkipIntent
from ..next_player_untap_model import ProhibitNextPlayerUntapIntent
from .earthbend_intents import EarthbendIntent, FinishEarthbendIntent, ReturnEarthbentObjectsIntent
from ..suspect_model import SuspectIntent
from ..preparation_model import PrepareSourceIntent
from ..combat_removal import CombatRemovalIntent, execute_combat_removal, unavailable_optional_combat_removal
from ..rules.temporary_cast_restrictions import apply_temporary_cast_restriction
from ..library_search_model import ProhibitLibrarySearchIntent
from ..rules.library_search_rules import apply_library_search_prohibition
from .stack_execution import STACK_INTENT_TYPES, execute_stack_intent
from .creation_execution import CREATION_INTENT_TYPES,execute_creation_intent
from .champion_execution import CHAMPION_INTENT_TYPES, execute_champion_intent

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .. import (
    destruction,
    permanent_exile,
    public_zone_moves,
    regeneration,
    return_to_hand,
    tap_state,
)
from ..destruction import DestructionHost
from ..permanent_exile import PermanentExileHost
from ..regeneration import RegenerationHost
from ..return_to_hand import ReturnToHandHost
from ..tap_state import TapStateHost
from ..unearth import resolve_unearth_intent, UnearthIntent
from ..combat_entry_activations import (
    EncoreTokensIntent,
    NinjutsuEntryIntent,
    resolve_encore_tokens,
    resolve_ninjutsu_entry,
)
from ..self_zone_move import resolve_self_zone_move, SelfZoneMoveIntent
from ..counter_removal import AllPermanentCountersThenTokenExileIntent
from ..public_tap_state_sets import (
    PublicTapStateSetError,
    SetPublicPermanentsTappedIntent,
    resolve_public_tap_state_set,
)
from .context import SemanticNodeError
from .intents import (
    AddManaIntent,
    AddSubtypeIntent,
    BecomeMonstrousIntent,
    BecomeRenownedIntent,
    BecomeMonarchIntent,
    CounterStackIntent,
    ChooseOneRestBottomRandomIntent,
    CopyControlledTokensIntent,
    CopyStackItemIntent,
    CreateTokenIntent,
    ChampionExileIntent,
    ReturnLinkedAbilityExileIntent,
    CreateRegenerationShieldIntent,
    CreateRegenerationShieldSetIntent,
    DealFixedDamageSetIntent,
    DomainEffectIntent,
    DestroyPermanentIntent,
    DestroyPermanentSetIntent,
    DestroyPermanentTargetsIntent,
    DrawCardsIntent,
    IntentPlan,
    EliminatePlayersIntent,
    WinGameIntent,
    ExploreCompletedIntent,
    ConniveCompletedIntent,
    ExilePermanentIntent,
    ExilePublicGraveyardCardIntent,
    GrantZoneObjectKeywordIntent,
    LifeChangeIntent,
    LibrarySelectionIntent,
    MoveObjectsSimultaneouslyIntent,
    MadnessChoiceIntent,
    MiracleCastChoiceIntent,
    MovePublicZoneSetIntent,
    MoveLibraryCardsToBottomIntent,
    MillCardsIntent,
    ImpulseAccessIntent,
    InspectHandIntent,
    ScryLibraryIntent,
    SurveilLibraryIntent,
    PayManaCostIntent,
    PayLifeIntent,
    PlaceCounterBatchIntent,
    PlaceCountersIntent,
    PlaceCountersOnSetIntent,
    PlaceCountersOnTargetsIntent,
    PlacePlayerCountersIntent,
    RemoveAllCountersIntent,
    RemoveCountersIntent,
    RecordChoiceIntent,
    RecordZoneMoveIntent,
    ReturnCardsToLibraryTopIntent,
    ReturnGraveyardCardToOwnerHandIntent,
    ReturnPermanentToOwnerHandIntent,
    ReorderLibraryTopIntent,
    RetargetStackItemIntent,
    RevealLibraryCardsIntent,
    RevealEachLibraryTopIntent,
    SetCardDesignationIntent,
    SetPermanentTappedIntent,
    ProhibitNextUntapIntent,
    SetPermanentsTappedIntent,
    ShuffleLibraryIntent,
    UntapAllCreaturesIntent,
    ZoneMoveIntent,
    ProliferateIntent,
)


class SemanticIntentSink(
    TapStateHost,
    DestructionHost,
    PermanentExileHost,
    ReturnToHandHost,
    RegenerationHost,
    Protocol,
):
    turn_steps: Any

    def apply_domain_effect_intent(self, intent: DomainEffectIntent) -> Any: ...

    def draw(
        self,
        seat: str,
        count: int = 1,
        *,
        reason: str = "draw",
        private: bool = False,
    ) -> list[str]: ...

    def become_monarch(self, seat: str, *, reason: str) -> str: ...

    def apply_mana_intent(self, intent: AddManaIntent) -> int: ...

    def set_card_designation_intent(
        self,
        intent: SetCardDesignationIntent,
    ) -> str: ...

    def become_monstrous_intent(
        self,
        intent: BecomeMonstrousIntent,
    ) -> object: ...

    def become_renowned_intent(
        self,
        intent: BecomeRenownedIntent,
    ) -> object: ...

    def record_choice_intent(self, intent: RecordChoiceIntent) -> None: ...

    def complete_explore_intent(
        self,
        intent: ExploreCompletedIntent,
    ) -> None: ...

    def complete_connive_intent(self, intent: ConniveCompletedIntent) -> None: ...

    def move_object_intent(self, intent: ZoneMoveIntent) -> str: ...
    def exploit_sacrifice_intent(self,intent: ExploitSacrificeIntent) -> str: ...

    def move_objects_simultaneously_intent(
        self,
        intent: MoveObjectsSimultaneouslyIntent,
        *,
        prepared_composition: Any = None,
    ) -> tuple[str, ...] | CommittedQuantityFollowup | int: ...

    def madness_choice_intent(self, intent: MadnessChoiceIntent) -> None: ...

    def choose_one_rest_bottom_random_intent(
        self,
        intent: ChooseOneRestBottomRandomIntent,
    ) -> tuple[str, ...]: ...

    def shuffle_library_intent(self, intent: ShuffleLibraryIntent) -> None: ...

    def return_cards_to_library_top_intent(
        self,
        intent: ReturnCardsToLibraryTopIntent,
    ) -> tuple[str, ...]: ...

    def record_zone_move_intent(self, intent: RecordZoneMoveIntent) -> None: ...

    def apply_life_change_intent(self, intent: LifeChangeIntent) -> int: ...
    def apply_life_gain_cost_batch_intent(self, intent: LifeGainCostBatchIntent) -> int: ...

    def apply_life_loss_batch_intent(self, intent: LifeLossBatchIntent) -> int: ...

    def pay_life_intent(self, intent: PayLifeIntent) -> int: ...

    def reveal_library_cards_intent(
        self,
        intent: RevealLibraryCardsIntent,
    ) -> tuple[str, ...]: ...

    def reveal_each_library_top_intent(self, intent: RevealEachLibraryTopIntent) -> object: ...

    def inspect_hand_intent(
        self,
        intent: InspectHandIntent,
    ) -> tuple[str, ...]: ...

    def reveal_colored_hand_cards_intent(self, intent: RevealColoredHandCardsIntent) -> tuple[str, ...]: ...

    def move_library_cards_to_bottom_intent(
        self,
        intent: MoveLibraryCardsToBottomIntent,
    ) -> tuple[str, ...]: ...

    def scry_library_intent(
        self,
        intent: ScryLibraryIntent,
    ) -> tuple[str, ...]: ...

    def surveil_library_intent(
        self,
        intent: SurveilLibraryIntent,
    ) -> object: ...

    def library_selection_intent(
        self,
        intent: LibrarySelectionIntent,
    ) -> tuple[str, ...]: ...

    def reorder_library_top_intent(
        self,
        intent: ReorderLibraryTopIntent,
    ) -> tuple[str, ...]: ...

    def pay_mana_cost_intent(self, intent: PayManaCostIntent) -> None: ...

    def gain_control_intent(self, intent: GainControlIntent | GiveControlIntent | GainSpellControlIntent) -> object: ...

    def gain_control_set_intent(self, intent: GainControlSetIntent | GiveControlSetIntent | RestoreOwnerControlIntent
                                | ExchangePermanentControlIntent) -> object: ...

    def place_counters_intent(
        self,
        intent: PlaceCountersIntent,
    ) -> tuple[str, ...]: ...

    def place_counter_batch_intent(
        self,
        intent: PlaceCounterBatchIntent,
    ) -> tuple[str, ...]: ...

    def place_counters_on_set_intent(
        self,
        intent: PlaceCountersOnSetIntent,
    ) -> tuple[str, ...]: ...

    def place_counters_on_targets_intent(
        self,
        intent: PlaceCountersOnTargetsIntent,
    ) -> tuple[str, ...]: ...

    def place_player_counters_intent(
        self,
        intent: PlacePlayerCountersIntent,
    ) -> tuple[str, ...]: ...

    def remove_counters_intent(
        self,
        intent: RemoveCountersIntent,
    ) -> Any: ...

    def remove_all_counters_intent(
        self,
        intent: RemoveAllCountersIntent | AllPermanentCountersThenTokenExileIntent,
    ) -> Any: ...

    def counter_stack_intent(self, intent: CounterStackIntent) -> None: ...

    def player_outcome_intent(self, intent: EliminatePlayersIntent | WinGameIntent) -> None: ...

    def copy_stack_item_intent(self, intent: CopyStackItemIntent) -> str | None: ...

    def retarget_stack_item_intent(
        self,
        intent: RetargetStackItemIntent,
    ) -> str: ...

    def create_token_intent(self, intent: CreateTokenIntent) -> tuple[str, ...]: ...

    def copy_controlled_tokens_intent(
        self,
        intent: CopyControlledTokensIntent,
    ) -> tuple[str, ...]: ...

    def add_subtype_intent(self, intent: AddSubtypeIntent) -> str: ...

    def grant_zone_object_keyword_intent(
        self,
        intent: GrantZoneObjectKeywordIntent,
    ) -> str | tuple[str, ...]: ...

    def proliferate_intent(self, intent: ProliferateIntent) -> CommittedQuantityFollowup | None: ...

    def deal_fixed_damage_set_intent(
        self,
        intent: DealFixedDamageSetIntent,
    ) -> Any: ...

    def destroy_permanent_set_intent(
        self,
        intent: DestroyPermanentSetIntent,
    ) -> Any: ...

    def move_public_zone_set_intent(
        self,
        intent: MovePublicZoneSetIntent,
    ) -> Any: ...

@dataclass(frozen=True, slots=True)
class DrawResolutionBatch:
    """Draw intents that must use the replacement-aware resolution path."""

    intents: tuple[DrawCardsIntent, ...]


@dataclass(frozen=True, slots=True)
class DrawResolutionRequest:
    current: DrawCardsIntent | None
    remaining_effects: tuple[dict[str, Any], ...]


def draw_resolution_batch(plan: IntentPlan) -> DrawResolutionBatch | None:
    if not all(isinstance(intent, DrawCardsIntent) for intent in plan.intents):
        return None
    return DrawResolutionBatch(
        intents=tuple(
            intent
            for intent in plan.intents
            if isinstance(intent, DrawCardsIntent)
        )
    )


def draw_intent_effect(intent: DrawCardsIntent) -> dict[str, Any]:
    """Serialize a queued typed draw without reintroducing untyped defaults."""

    effect = {
        "op": (
            "draw_with_actions"
            if intent.post_draw_actions
            else "draw"
        ),
        "player": intent.player,
        "count": intent.count,
        "private": intent.private,
        "reason": intent.reason,
    }
    if intent.post_draw_actions:
        effect["post_draw_actions"] = [
            action.to_dict() for action in intent.post_draw_actions
        ]
    return effect


def prepare_draw_resolution(
    plan: IntentPlan,
    following_effects: tuple[Mapping[str, Any], ...],
) -> DrawResolutionRequest | None:
    batch = draw_resolution_batch(plan)
    if batch is None:
        return None
    current = batch.intents[0] if batch.intents else None
    return DrawResolutionRequest(
        current=current,
        remaining_effects=(
            *(
                draw_intent_effect(intent)
                for intent in batch.intents[1:]
            ),
            *(dict(effect) for effect in following_effects),
        ),
    )


PermanentObjectIntent = (
    SelectMutateMergeIntent |
    ResumeCompositionOrderIntent |
    PairSoulbondIntent | GainControlIntent | GainSpellControlIntent | GainControlSetIntent | GiveControlIntent | GiveControlSetIntent | RestoreOwnerControlIntent | ExchangePermanentControlIntent | CreateRegenerationShieldIntent
    | CreateRegenerationShieldSetIntent
    | ChampionExileIntent | ReturnLinkedAbilityExileIntent
    | EncodeCipherIntent
    | PrepareSourceIntent
    | DestroyPermanentIntent
    | DestroyPermanentSetIntent
    | DestroyPermanentTargetsIntent
    | ExilePermanentIntent
    | ExilePublicGraveyardCardIntent
    | CombatRemovalIntent
    | MovePublicZoneSetIntent
    | ReturnPermanentToOwnerHandIntent
    | ReturnGraveyardCardToOwnerHandIntent
)
PERMANENT_OBJECT_INTENT_TYPES = (
    SelectMutateMergeIntent,
    ResumeCompositionOrderIntent,
    EncodeCipherIntent,
    PrepareSourceIntent,
    *CHAMPION_INTENT_TYPES,
    PairSoulbondIntent,
    GainControlIntent, GainSpellControlIntent, GainControlSetIntent, GiveControlIntent, GiveControlSetIntent,
    RestoreOwnerControlIntent, ExchangePermanentControlIntent,
    CreateRegenerationShieldIntent,
    CreateRegenerationShieldSetIntent,
    DestroyPermanentIntent,
    DestroyPermanentSetIntent,
    DestroyPermanentTargetsIntent,
    ExilePermanentIntent,
    ExilePublicGraveyardCardIntent,
    CombatRemovalIntent,
    MovePublicZoneSetIntent,
    ReturnPermanentToOwnerHandIntent,
    ReturnGraveyardCardToOwnerHandIntent,
)


def _destroy_permanent_with_result(sink, intent):
    from ..rules.destroyed_creature_copy import DestroyedCreatureCopy, commit_destroyed_creature_copy

    if isinstance(intent.followup, DestroyedCreatureCopy):
        return commit_destroyed_creature_copy(sink, intent)
    target = sink._resolve_object(intent.actor, intent.object_ref, zones={"battlefield"})
    original = None
    if isinstance(intent.followup, TargetControllerLibraryReveal):
        original = (target.object_id, target.logical_object_id, target_controller_library_facts(sink, target))
    types, _, supertypes = sink._type_parts(str(sink._effective_card_data(target).get("type_line") or ""))
    result = destruction.destroy_permanent_refs(
        sink, (intent.object_ref,), actor=intent.actor, reason=intent.reason,
        replacement_selections=intent.replacement_selections,
    )
    if original is not None:
        object_id, identity, facts = original
        return committed_target_controller_library_result(sink, target_ref=intent.object_ref,
            target_object_id=object_id, target_identity=identity, caster=intent.actor, before=facts)
    if isinstance(intent.followup, NonbasicLandSourceUntap):
        return intent.followup.committed_result(target_was_basic="basic" in supertypes)
    return intent.followup.committed_result(artifact="artifact" in types, destroyed=target.object_id in result.destroyed_object_ids)


def _destroy_target_set_with_followup(sink, intent):
    from ..rules.controlled_destruction_source_counters import (
        ControlledDestructionSourceCounters, commit_controlled_destruction_source_counters,
    )

    if isinstance(intent.followup, ControlledDestructionSourceCounters):
        return commit_controlled_destruction_source_counters(sink, intent)
    originals = tuple(sink._resolve_object(intent.actor, ref, zones={"battlefield"}) for ref in intent.object_refs)
    controllers = {card.object_id: card.controller for card in originals}
    result = destruction.destroy_permanent_refs(
        sink, intent.object_refs, actor=intent.actor, reason=intent.reason,
        replacement_selections=intent.replacement_selections,
        record_graveyard_outcomes=intent.followup.requires_graveyard,
    )
    committed = result.died_object_ids if intent.followup.requires_graveyard else result.destroyed_object_ids
    return intent.followup.from_committed_controllers(
        controllers[object_id] for object_id in committed if object_id in controllers
    )


def _execute_permanent_object_intent(
    sink: SemanticIntentSink,
    intent: PermanentObjectIntent,
) -> tuple[str, object]:
    if isinstance(intent, PrepareSourceIntent):
        from ..preparation import resolve_prepare_source

        return "preparation", resolve_prepare_source(sink, intent)
    if isinstance(intent, CombatRemovalIntent):
        return intent.object_ref, execute_combat_removal(sink, intent)
    if isinstance(intent,SelectMutateMergeIntent):
        from ..rules.mutate_resolution import commit_mutate_merge
        return intent.actor,commit_mutate_merge(sink,intent)
    if isinstance(intent,ResumeCompositionOrderIntent):
        from ..composition_ordering import resume_composition_order
        return intent.actor,resume_composition_order(sink,intent)
    if isinstance(intent, EncodeCipherIntent):
        from ..rules.cipher import encode_cipher
        return 'cipher', encode_cipher(sink, intent)
    if isinstance(intent, CHAMPION_INTENT_TYPES):
        return execute_champion_intent(sink, intent)
    if isinstance(intent, PairSoulbondIntent):
        from ..soulbond_state import commit_soulbond_pair
        pair = commit_soulbond_pair(sink, pair_id=intent.pair_id,
            source_ref=intent.source_ref, partner_ref=intent.partner_ref, controller=intent.actor,
            source_identity=intent.source_identity, partner_identity=intent.partner_identity,
            entered_ref=intent.entered_ref, entered_identity=intent.entered_identity)
        return 'pair', pair.pair_id
    if isinstance(intent, (GainControlIntent, GiveControlIntent, GainSpellControlIntent)):
        return intent.object_ref, sink.gain_control_intent(intent)
    if isinstance(intent, (GainControlSetIntent, GiveControlSetIntent, RestoreOwnerControlIntent, ExchangePermanentControlIntent)):
        return intent.actor, sink.gain_control_set_intent(intent)
    if isinstance(intent, CreateRegenerationShieldSetIntent):
        return intent.actor, tuple(_execute_permanent_object_intent(sink, shield)[1] for shield in intent.shields)
    if isinstance(intent, CreateRegenerationShieldIntent):
        return (
            intent.object_ref,
            regeneration.create_regeneration_shield(
                sink,
                intent.object_ref,
                actor=intent.actor,
                reason=intent.reason,
                logical_object_id=intent.logical_object_id,
            ),
        )
    if isinstance(intent, DestroyPermanentIntent):
        if intent.followup is not None:
            return intent.object_ref, _destroy_permanent_with_result(sink, intent)
        return (
            intent.object_ref,
            destruction.destroy_permanent_refs(
                sink,
                (intent.object_ref,),
                actor=intent.actor,
                reason=intent.reason,
                regeneration_prohibited=intent.regeneration_prohibited,
                replacement_selections=intent.replacement_selections,
            ),
        )
    if isinstance(intent, DestroyPermanentSetIntent):
        return intent.actor, sink.destroy_permanent_set_intent(intent)
    if isinstance(intent, DestroyPermanentTargetsIntent):
        if intent.followup is not None:
            return intent.actor, _destroy_target_set_with_followup(sink, intent)
        return (
            intent.actor,
            destruction.destroy_permanent_refs(
                sink,
                intent.object_refs,
                actor=intent.actor,
                reason=intent.reason,
                regeneration_prohibited=intent.regeneration_prohibited,
                replacement_selections=intent.replacement_selections,
            ),
        )
    if isinstance(intent, ExilePermanentIntent):
        return (
            intent.object_ref,
            permanent_exile.exile_permanent(
                sink,
                intent.object_ref,
                actor=intent.actor,
                reason=intent.reason,
                replacement_selections=intent.replacement_selections,
            ),
        )
    if isinstance(intent, GraveyardCardCounterLifeIntent):
        from ..rules.graveyard_card_results import commit_graveyard_card_counter_life
        return intent.object_ref, commit_graveyard_card_counter_life(sink, intent)
    if isinstance(intent, GraveyardExileBranchIntent):
        from ..rules.graveyard_exile_branches import commit_graveyard_exile_branch

        return intent.object_ref, commit_graveyard_exile_branch(sink, intent)
    if isinstance(intent, GraveyardExileTokenIntent):
        from ..rules.graveyard_exile_tokens import commit_graveyard_exile_token

        return intent.object_ref, commit_graveyard_exile_token(sink, intent)
    if isinstance(intent, GraveyardExileTypeOutcomeIntent):
        from ..rules.graveyard_exile_type_outcomes import commit_graveyard_exile_type_outcomes

        return intent.object_ref, commit_graveyard_exile_type_outcomes(sink, intent)
    if isinstance(intent, ExilePublicGraveyardCardIntent):
        if intent.linked_source is not None:
            from ..linked_ability_exile_state import exile_graveyard_card_for_linked_ability
            return intent.object_ref, exile_graveyard_card_for_linked_ability(
                sink, object_ref=intent.object_ref, source=intent.linked_source, ability_pair=intent.linked_pair,
                actor=intent.actor, reason=intent.reason, replacement_selections=intent.replacement_selections,
            )
        return (
            intent.object_ref,
            public_zone_moves.exile_public_graveyard_card(
                sink,
                intent.object_ref,
                actor=intent.actor,
                reason=intent.reason,
                replacement_selections=intent.replacement_selections,
            ),
        )
    if isinstance(intent, MovePublicZoneSetIntent):
        return intent.actor, sink.move_public_zone_set_intent(intent)
    if isinstance(intent, ReturnPermanentToOwnerHandIntent):
        return (
            intent.object_ref,
            return_to_hand.return_permanent_to_owner_hand(
                sink,
                intent.object_ref,
                actor=intent.actor,
                reason=intent.reason,
                replacement_selections=intent.replacement_selections,
            ),
        )
    return (
        intent.object_ref,
        return_to_hand.return_graveyard_card_to_owner_hand(
            sink,
            intent.object_ref,
            actor=intent.actor,
            reason=intent.reason,
            replacement_selections=intent.replacement_selections,
            target_snapshot=intent.target_snapshot,
            scope=intent.scope,
        ),
    )


RecordingIntent = RecordChoiceIntent | ExploreCompletedIntent | ConniveCompletedIntent | InspectHandIntent | RevealColoredHandCardsIntent
RECORDING_INTENT_TYPES = (
    RecordChoiceIntent,
    ExploreCompletedIntent,
    ConniveCompletedIntent,
    InspectHandIntent,
    RevealColoredHandCardsIntent,
)


def _execute_recording_intent(
    sink: SemanticIntentSink,
    intent: RecordingIntent,
) -> tuple[str, None]:
    if isinstance(intent, RecordChoiceIntent):
        sink.record_choice_intent(intent)
        return intent.actor, None
    if isinstance(intent, InspectHandIntent):
        return intent.player, sink.inspect_hand_intent(intent)
    if isinstance(intent,RevealColoredHandCardsIntent):
        return intent.actor,sink.reveal_colored_hand_cards_intent(intent)
    if isinstance(intent, ConniveCompletedIntent):
        sink.complete_connive_intent(intent)
        return intent.conniver.ref, None
    sink.complete_explore_intent(intent)
    return intent.explorer_ref, None


LibraryIntent = (
    ChooseOneRestBottomRandomIntent
    | ShuffleLibraryIntent
    | ReturnCardsToLibraryTopIntent
    | RevealLibraryCardsIntent
    | RevealEachLibraryTopIntent
    | MoveLibraryCardsToBottomIntent
    | ScryLibraryIntent
    | SurveilLibraryIntent
    | LibrarySelectionIntent
    | ReorderLibraryTopIntent
)
LIBRARY_INTENT_TYPES = (
    ChooseOneRestBottomRandomIntent,
    ShuffleLibraryIntent,
    ReturnCardsToLibraryTopIntent,
    RevealLibraryCardsIntent,
    RevealEachLibraryTopIntent,
    MoveLibraryCardsToBottomIntent,
    ScryLibraryIntent,
    SurveilLibraryIntent,
    LibrarySelectionIntent,
    ReorderLibraryTopIntent,
)


def _execute_library_intent(
    sink: SemanticIntentSink,
    intent: LibraryIntent,
) -> tuple[str, object]:
    if isinstance(intent, ChooseOneRestBottomRandomIntent):
        return intent.player, sink.choose_one_rest_bottom_random_intent(intent)
    if isinstance(intent, ShuffleLibraryIntent):
        sink.shuffle_library_intent(intent)
        return intent.player, None
    if isinstance(intent, ReturnCardsToLibraryTopIntent):
        return intent.player, sink.return_cards_to_library_top_intent(intent)
    if isinstance(intent, RevealLibraryCardsIntent):
        return intent.player, sink.reveal_library_cards_intent(intent)
    if isinstance(intent, RevealEachLibraryTopIntent):
        return intent.actor, sink.reveal_each_library_top_intent(intent)
    if isinstance(intent, MoveLibraryCardsToBottomIntent):
        return intent.player, sink.move_library_cards_to_bottom_intent(intent)
    if isinstance(intent, ScryLibraryIntent):
        return intent.player, sink.scry_library_intent(intent)
    if isinstance(intent, SurveilLibraryIntent):
        return intent.player, sink.surveil_library_intent(intent)
    if isinstance(intent, LibrarySelectionIntent):
        return intent.player, sink.library_selection_intent(intent)
    return intent.player, sink.reorder_library_top_intent(intent)


CounterPlacementIntent = (
    PlaceCounterBatchIntent
    | PlaceCountersIntent
    | PlaceCountersOnSetIntent
    | PlaceCountersOnTargetsIntent
    | PlacePlayerCountersIntent
)
COUNTER_PLACEMENT_INTENT_TYPES = (
    PlaceCounterBatchIntent,
    PlaceCountersIntent,
    PlaceCountersOnSetIntent,
    PlaceCountersOnTargetsIntent,
    PlacePlayerCountersIntent,
)
_COUNTER_RESULT_KEY = "counters"


def _execute_counter_placement_intent(
    sink: SemanticIntentSink,
    intent: CounterPlacementIntent,
) -> tuple[str, object]:
    if isinstance(intent, PlaceCounterBatchIntent):
        return _COUNTER_RESULT_KEY, sink.place_counter_batch_intent(intent)
    if isinstance(intent, PlaceCountersIntent):
        from .source_counter_reflexive_intent import PlaceSourceCounterReflexiveIntent

        if isinstance(intent, PlaceSourceCounterReflexiveIntent):
            from ..rules.source_counter_reflexive import commit_source_counter_reflexive

            return _COUNTER_RESULT_KEY, commit_source_counter_reflexive(sink, intent)
        return _COUNTER_RESULT_KEY, sink.place_counters_intent(intent)
    if isinstance(intent, PlaceCountersOnSetIntent):
        return _COUNTER_RESULT_KEY, sink.place_counters_on_set_intent(intent)
    if isinstance(intent, PlaceCountersOnTargetsIntent):
        return _COUNTER_RESULT_KEY, sink.place_counters_on_targets_intent(intent)
    return "player_counters", sink.place_player_counters_intent(intent)


PlayerIntent = (
    BecomeMonarchIntent | MillCardsIntent | ImpulseAccessIntent | DiscardWholeHandsIntent
    | DiscardWholeHandsResultIntent | HandLibraryRedrawIntent | RandomDiscardIntent | RandomGraveyardReturnIntent
    | GraveyardRecyclingIntent | FixedSourceSacrificeIntent
    | TemporaryCastRestrictionIntent | TemporaryDrawRedirectIntent | TargetCombatDamageMultiplierIntent | ScheduleNextDrawResultIntent | ProhibitLibrarySearchIntent | ScheduleTurnSkipIntent | ScheduleExtraCombatIntent
    | ScheduleAdditionalBeginningIntent | NoMaximumForGameIntent
    | TemporaryProtectedAttackPaymentIntent
    | NextTurnAttackRestrictionIntent
    | EarthbendIntent | FinishEarthbendIntent | ReturnEarthbentObjectsIntent | SuspectIntent | TokenMillRepeatIntent | LibraryBottomResultIntent
    | VentureProgressIntent | TakeInitiativeIntent
)
PLAYER_INTENT_TYPES = (
    BecomeMonarchIntent, MillCardsIntent, ImpulseAccessIntent, DiscardWholeHandsIntent,
    DiscardWholeHandsResultIntent, HandLibraryRedrawIntent, RandomDiscardIntent, RandomGraveyardReturnIntent,
    GraveyardRecyclingIntent, TargetLandTypeSettingIntent, FixedSourceSacrificeIntent,
    TemporaryCastRestrictionIntent, TemporaryDrawRedirectIntent, TargetCombatDamageMultiplierIntent, ScheduleNextDrawResultIntent, ProhibitLibrarySearchIntent, ScheduleTurnSkipIntent, ScheduleExtraCombatIntent, ScheduleAdditionalBeginningIntent, NoMaximumForGameIntent,
    TemporaryProtectedAttackPaymentIntent,
    NextTurnAttackRestrictionIntent,
    EarthbendIntent, FinishEarthbendIntent, ReturnEarthbentObjectsIntent, SuspectIntent, TokenMillRepeatIntent, LibraryBottomResultIntent,
    VentureProgressIntent, TakeInitiativeIntent,
)
TapStateIntent = (
    SetPermanentTappedIntent
    | ProhibitNextUntapIntent
    | tap_state.ProhibitAttackingCreaturesNextUntapIntent
    | ProhibitNextPlayerUntapIntent
    | SetPermanentsTappedIntent
    | SetPublicPermanentsTappedIntent
)
TAP_STATE_INTENT_TYPES = (
    SetPermanentTappedIntent,
    ProhibitNextUntapIntent,
    tap_state.ProhibitAttackingCreaturesNextUntapIntent,
    ProhibitNextPlayerUntapIntent,
    SetPermanentsTappedIntent,
    SetPublicPermanentsTappedIntent,
)


def _execute_player_intent(
    sink: SemanticIntentSink,
    intent: PlayerIntent,
) -> tuple[str, object]:
    if isinstance(intent, FixedSourceSacrificeIntent):
        from .source_sacrifice import apply_fixed_source_sacrifice
        return intent.actor, apply_fixed_source_sacrifice(sink, intent)
    if isinstance(intent, TargetLandTypeSettingIntent):
        from ..rules.target_land_type import apply_target_land_type
        return intent.actor, apply_target_land_type(sink, intent)
    if isinstance(intent, GraveyardRecyclingIntent):
        from ..rules.graveyard_recycling import execute_graveyard_recycling
        return intent.actor, execute_graveyard_recycling(sink, intent)
    if isinstance(intent, TakeInitiativeIntent):
        from ..initiative_runtime import commit_take_initiative

        return intent.player, commit_take_initiative(sink, intent)
    if isinstance(intent, VentureProgressIntent):
        from ..dungeon_runtime import commit_venture_progress

        return intent.offer.owner, commit_venture_progress(sink, intent)
    if isinstance(intent, EarthbendIntent):
        from ..earthbend import animate_earthbend_land
        return intent.actor, animate_earthbend_land(sink, intent)
    if isinstance(intent, SuspectIntent):
        from ..permanent_designations import apply_suspect_designation
        return intent.actor, apply_suspect_designation(sink, intent)
    if isinstance(intent, FinishEarthbendIntent):
        from ..earthbend import finish_earthbend_land
        return intent.actor, finish_earthbend_land(sink, intent)
    if isinstance(intent, ReturnEarthbentObjectsIntent):
        from ..earthbend import return_earthbent_objects
        return intent.actor, return_earthbent_objects(sink, intent)
    if isinstance(intent, TokenMillRepeatIntent):
        return intent.actor, execute_token_mill_repeat(sink, intent)
    if isinstance(intent, LibraryBottomResultIntent):
        return intent.actor, commit_library_bottom_creature_result(sink, intent)
    if isinstance(intent, ScheduleExtraCombatIntent):
        from .extra_combat import execute_extra_combat_intent

        return intent.actor, execute_extra_combat_intent(sink, intent)
    if isinstance(intent, ScheduleAdditionalBeginningIntent):
        sink.turn_steps.schedule_additional_beginning(intent.player, source=intent.source_ref)
        return intent.actor, None
    if isinstance(intent, ScheduleTurnSkipIntent):
        return intent.actor, tuple(sink.turn_steps.schedule_skip(seat, intent.spec) for seat in intent.players)
    if isinstance(intent,ProhibitLibrarySearchIntent):return intent.actor,apply_library_search_prohibition(sink,actor=intent.actor,source_id=intent.source_id)
    if isinstance(intent, RandomDiscardIntent):
        return intent.actor, execute_random_discard(sink, intent)
    if isinstance(intent, RandomGraveyardReturnIntent):
        return intent.actor, execute_random_graveyard_return(sink,intent)
    if isinstance(intent, TemporaryProtectedAttackPaymentIntent):
        from ..rules.protected_attack_payments import apply_protected_attack_payment
        return intent.actor, apply_protected_attack_payment(sink, intent)
    if isinstance(intent, NoMaximumForGameIntent):
        from .game_hand_size import commit_no_maximum_for_game

        return intent.controller, commit_no_maximum_for_game(sink, intent).effect_id
    if isinstance(intent, NextTurnAttackRestrictionIntent):
        from ..rules.next_turn_attack_restrictions import apply_next_turn_attack_restriction
        return intent.actor, apply_next_turn_attack_restriction(sink, intent)
    if isinstance(intent, ScheduleNextDrawResultIntent):
        from .scheduled_next_draw import apply_next_draw_result_intent

        return intent.actor, apply_next_draw_result_intent(sink, intent).effect_id
    if isinstance(intent, TargetCombatDamageMultiplierIntent):
        from .target_combat_damage_multiplier import apply_target_combat_damage_multiplier

        return intent.actor, apply_target_combat_damage_multiplier(sink, intent)
    if isinstance(intent, TemporaryDrawRedirectIntent):
        from .temporary_draw_redirect import apply_temporary_draw_redirect

        return intent.actor, apply_temporary_draw_redirect(sink, intent)
    if isinstance(intent, TemporaryCastRestrictionIntent):
        return intent.actor,apply_temporary_cast_restriction(sink,actor=intent.actor,restriction=intent.restriction,
            target_player=intent.target_player,source_id=intent.source_id)
    if isinstance(intent, DiscardWholeHandsIntent):
        return intent.actor, execute_whole_hand_discard(sink, intent)
    if isinstance(intent, DiscardWholeHandsResultIntent):return intent.actor, execute_discard_result(sink, intent)
    if isinstance(intent, HandLibraryRedrawIntent):
        from ..rules.hand_library_redraw import execute_hand_library_redraw
        return intent.actor, execute_hand_library_redraw(sink, intent)
    if isinstance(intent, BecomeMonarchIntent):
        return intent.player, sink.become_monarch(
            intent.player,
            reason=intent.reason,
        )
    if isinstance(intent, ImpulseAccessIntent):
        from ..impulse_access import (
            ImpulseAccessRequest,
            resolve_fixed_impulse_access,
        )

        result = resolve_fixed_impulse_access(
            sink,
            ImpulseAccessRequest(
                actor=intent.actor,
                player=intent.player,
                count=intent.count,
                duration=intent.duration,
                reason=intent.reason,
                schema_version=intent.schema_version,
            ),
        )
        return intent.player, result.exiled_refs
    from ..milling import mill_cards, mill_player_batch, mill_request

    if intent.batch_players:
        results = mill_player_batch(sink, tuple(
            mill_request(sink, actor=intent.actor, player=player, count=intent.count, reason=intent.reason)
            for player in intent.batch_players
        ), replacement_selections=intent.replacement_selections)
        if intent.followup is not None:
            return intent.actor, intent.followup.from_committed_results(sink, results)
        return intent.actor, tuple(ref for result in results for ref in result.refs)

    result = mill_cards(
        sink,
        mill_request(
            sink, actor=intent.actor,
            player=intent.player,
            count=intent.count,
            reason=intent.reason,
        ),
        replacement_selections=intent.replacement_selections,
    )
    if intent.followup is not None:
        return intent.player, intent.followup.from_committed_result(sink, result)
    return intent.player, result.refs


def _execute_tap_state_intent(
    sink: SemanticIntentSink,
    intent: TapStateIntent,
) -> tuple[str, object]:
    if isinstance(intent, tap_state.ProhibitAttackingCreaturesNextUntapIntent):
        return intent.actor, tap_state.prohibit_attacking_creatures_next_untap(sink, intent)
    if isinstance(intent, ProhibitNextPlayerUntapIntent):
        return intent.restriction.player, tap_state.schedule_next_player_untap(sink, intent)
    if isinstance(intent, ProhibitNextUntapIntent):
        return intent.object_ref, tap_state.prohibit_next_untap(sink, intent.object_ref,
            actor=intent.actor, reason=intent.reason, logical_object_id=intent.logical_object_id,
            step_count=intent.step_count)
    if isinstance(intent, SetPermanentTappedIntent):
        result = tap_state.set_permanent_tapped(
            sink,
            intent.object_ref,
            actor=intent.actor,
            tapped=intent.tapped,
            reason=intent.reason,
            logical_object_id=intent.logical_object_id,
            untap_restraint=intent.untap_restraint,
        )
        return intent.object_ref, result
    if isinstance(intent, SetPublicPermanentsTappedIntent):
        try:
            result = resolve_public_tap_state_set(
                sink,
                actor=intent.actor,
                spec=intent.spec,
                tapped=intent.tapped,
                reason=intent.reason,
                source_ref=intent.source_ref,
                untap_restraint=intent.untap_restraint,
                lose_unspent_mana=intent.lose_unspent_mana,
                restrain_snapshot_on_player_untap=intent.restrain_snapshot_on_player_untap,
            )
        except PublicTapStateSetError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return intent.actor, result
    cards = tuple(
        next(card for card in sink.state.cards.values() if card.ref == object_ref)
        for object_ref in intent.object_refs
    )
    prior_states = {card.object_id: card.tapped for card in cards}
    result = tuple(
        tap_state.set_permanent_tapped(
            sink,
            object_ref,
            actor=intent.actor,
            tapped=intent.tapped,
            reason=intent.reason,
            semantic_events=False,
        )
        for object_ref in intent.object_refs
    )
    tap_state.dispatch_tap_state_group(
        sink, (card for card in cards if card.tapped != prior_states[card.object_id]),
        tapped=intent.tapped, reason=intent.reason,
    )
    if intent.prohibit_next_untap:
        for card in cards:
            tap_state.prohibit_next_untap(sink, card.ref, actor=intent.actor, reason=intent.reason,
                                         logical_object_id=card.logical_object_id)
    return intent.actor, result


def _execute_resolving_cast_choice(sink, intent):
    if isinstance(intent,ResolveRippleCastIntent):
        from ..rules.ripple_casting import execute_ripple_cast
        return intent.actor,execute_ripple_cast(sink,intent)
    if isinstance(intent,RippleBottomIntent):
        from ..rules.revealed_library_bottom import execute_ripple_bottom
        return intent.actor,execute_ripple_bottom(sink,intent)
    if isinstance(intent, ResolveBoundedHandCastIntent):
        from ..rules.bounded_hand_cast import execute_bounded_hand_cast
        return intent.actor, execute_bounded_hand_cast(sink, intent)
    if isinstance(intent, RevealCapturedDrawsIntent):
        from ..drawing.captured_hand_casting import commit_captured_draw_reveal
        return intent.actor, commit_captured_draw_reveal(sink, intent)
    if isinstance(intent, ResolveCapturedDrawCastIntent):
        from ..drawing.captured_hand_casting import execute_captured_draw_cast
        return intent.actor, execute_captured_draw_cast(sink, intent)
    if isinstance(intent, ResolvingSharedTypeLibraryPlayIntent):
        from ..rules.shared_type_library_play import execute_shared_type_library_play
        return intent.actor, execute_shared_type_library_play(sink, intent)
    if isinstance(intent, ResolvingExileLandPlayIntent):
        sink._play_land(intent.actor, {}, resolving=intent)
        return intent.actor, None
    if isinstance(intent, ExileGraveyardCopiesIntent):
        from ..semantic_choices.graveyard_copy_cast import execute_exile_graveyard_copies
        return intent.actor, execute_exile_graveyard_copies(sink, intent)
    if isinstance(intent, GraveyardCastIntent):
        from ..semantic_choices.targeted_graveyard_cast import execute_targeted_graveyard_cast
        execute_targeted_graveyard_cast(sink, intent)
        return intent.effect["player"], None
    if isinstance(intent,DiscoverCastIntent):
        from ..discover_casting import execute_discover_cast
        execute_discover_cast(sink,intent)
        return intent.actor, None
    if isinstance(intent,DiscoverCleanupIntent):
        from ..discover_cleanup import execute_discover_cleanup
        execute_discover_cleanup(sink,intent)
        return intent.actor, None
    if isinstance(intent, MiracleCastChoiceIntent):
        from ..drawing.miracle_casting import execute_miracle_cast_choice
        execute_miracle_cast_choice(sink, intent)
    else:
        sink.madness_choice_intent(intent)
    return intent.source.card_ref or intent.actor, None


def _synchronize_instruction_observers(sink):
    from ..rules.city_blessing import synchronize_citys_blessing
    synchronize_citys_blessing(sink, reason='semantic instruction committed')
    from ..rules.state_triggers import observe_public_state_triggers
    observe_public_state_triggers(sink)
    if getattr(getattr(sink, 'state', None), 'soulbond_pairs', None):
        from ..soulbond_state import reconcile_soulbond_pairs
        reconcile_soulbond_pairs(sink)


def _optional_result_scope(host, item, instruction):
    from ..variable_resolution_mana_model import is_optional_target_mana_choice

    limited_mana = is_optional_target_mana_choice(instruction)
    if not limited_mana and instruction.get("op") != "offer_optional_effect":
        return None, True
    from ..rules.trigger_limits import optional_result_usage_available, optional_result_usage_binding

    try:
        binding = optional_result_usage_binding(host, item)
        if limited_mana and binding is None:
            raise ValueError("Optional opponent mana requires its original triggered ability")
        return binding, binding is None or optional_result_usage_available(host.state, binding)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc


def _can_choose_limited_optional_result(host, effect: Mapping[str, Any]) -> bool:
    from ..permanent_event_state_model import optional_draw_proliferate_is_closed

    actor = effect['player']
    children = effect['effects']
    if optional_draw_proliferate_is_closed(children, player=actor):
        child = children[0]
    else:
        child, = children
    operation = child['op']
    if operation == 'draw':
        from .draw_restrictions import current_draw_permission

        return current_draw_permission(host, actor).allows_complete_draw(child['count'])
    if operation == 'life' and child['delta'] > 0:
        from .life_prohibitions import collect_life_gain_permissions

        return collect_life_gain_permissions(host).allows_gain(actor)
    if operation == 'offer_optional_mana_payment':
        return bool(host.state.players[actor].zones['hand'])
    return True


def _prepare_available_optional_effect(host, item, raw_effect, effect):
    """Filter impossible choices using each represented result owner."""
    from ..combat_removal import prepare_optional_blocked_attacker_return
    from ..event_card_return import unavailable_optional_event_library_move
    from ..effect_runtime.linked_library_imprint import unavailable_optional_linked_artifact_return
    from ..permanent_transform import prepare_optional_source_transform
    from ..rules.attachment_actions import unavailable_optional_equipment_attachment
    from ..rules.graveyard_aura_entry import prepare_optional_graveyard_aura_entry

    if unavailable_optional_combat_removal(host, raw_effect, effect):
        return None
    try:
        effect = prepare_optional_source_transform(host, item, raw_effect, effect)
        if effect is not None:
            effect = prepare_optional_blocked_attacker_return(host, item, raw_effect, effect)
        if effect is None:
            return None
        if (unavailable_optional_equipment_attachment(host, raw_effect, effect)
            or unavailable_optional_event_library_move(host, raw_effect, effect)
            or unavailable_optional_linked_artifact_return(host, item, effect)):
            return None
        return prepare_optional_graveyard_aura_entry(host, item, raw_effect, effect)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc


def _is_zero_declared_result(effect, raw_effect, item):
    from ..entry_x_amount_model import is_zero_entry_x_token_batch

    if is_zero_announced_x_player_round(raw_effect, effect) or is_zero_entry_x_token_batch(raw_effect, effect):
        return True
    if type(effect.get("amount")) is not int or effect["amount"] != 0:
        return False
    if effect.get("op") in {"place_counters", "place_counters_on_set"}:
        from ..counter_placement_sets import uses_source_power_combat_counter_set

        return uses_source_power_combat_counter_set(raw_effect)
    if effect.get("op") != "place_counters_on_targets":
        return False
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_KIND, ScalarAmountOrigin
    from ..source_entry_cast_information import uses_paid_white_entry_amount
    from ..split_two_counter_target_amount import is_optional_split_two_counter_amount

    raw_amount = raw_effect.get("amount")
    return (
        not item.targets and is_optional_split_two_counter_amount(raw_amount)
        or uses_paid_white_entry_amount(raw_amount)
        or isinstance(raw_amount, Mapping) and raw_amount.get("kind") == SCALAR_AMOUNT_KIND
        and raw_amount.get("origin") == ScalarAmountOrigin.CAST_SPELL_MANA_VALUE.value
    )


def _bind_event_resolution_results(host, item, raw_effect):
    from ..rules.casting.spell_text import scoped_cast_text_instruction
    from ..rules.event_player_result_shapes import bind_event_player_count_effect
    from ..rules.fractional_player_result_shapes import bind_half_library_draw
    from ..rules.other_spell_history_shapes import bind_other_spell_draw
    from ..rules.scheduled_player_shapes import bind_upkeep_source_counter_draw
    from ..rules.owner_library_position_shapes import bind_draw_source_owner_top_move
    from ..rules.attack_batches import bind_attacking_opponent_token
    from ..rules.death_controller_opponents import bind_death_opponent_result
    try:
        scoped_item, raw_effect = scoped_cast_text_instruction(item, raw_effect)
        raw_effect = bind_draw_source_owner_top_move(scoped_item, raw_effect)
        raw_effect = bind_half_library_draw(host.state, scoped_item, raw_effect)
        raw_effect = bind_event_player_count_effect(host.state, scoped_item, raw_effect)
        raw_effect = bind_upkeep_source_counter_draw(host, scoped_item, raw_effect)
        raw_effect = bind_upkeep_source_counter_sacrifice(host, scoped_item, raw_effect)
        raw_effect = bind_counter_scaled_annihilator(host, scoped_item, raw_effect)
        raw_effect = bind_other_spell_draw(host.state, scoped_item, raw_effect)
        raw_effect = bind_qualified_token_creators(host, raw_effect)
        if raw_effect is not None:
            raw_effect = bind_death_opponent_result(host, scoped_item, raw_effect)
            raw_effect = bind_attacking_opponent_token(host, scoped_item, raw_effect)
            from ..rules.cast_coin_transform import bind_owner_transform_resolution

            raw_effect = bind_owner_transform_resolution(host, scoped_item, raw_effect)
    except ValueError as exc:
        raise SemanticNodeError(str(exc)) from exc
    return scoped_item, raw_effect


def _prepare_source_bound_resolution(host, item, raw_effect, instruction_pointer):
    """Resolve typed source-bound instructions before generic resolution binding."""
    from ..rules.attached_copy_payment import uses_attached_copy_payment

    if uses_attached_copy_payment(raw_effect):
        from ..token_copy_references import bind_attached_copy_payment

        try:
            return True, bind_attached_copy_payment(host, item, raw_effect)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.upkeep_power_counter_balance import bind_upkeep_power_counter_balance, has_upkeep_power_counter_effect

    if has_upkeep_power_counter_effect(raw_effect):
        try:
            return True, bind_upkeep_power_counter_balance(host, item, raw_effect)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rad_counters import bind_rad_instruction, is_rad_instruction

    if is_rad_instruction(raw_effect):
        return True, bind_rad_instruction(host, item, raw_effect)
    from ..rules.faction_trigger_model import FACTION_RESULT_FIELD

    if FACTION_RESULT_FIELD in raw_effect:
        from ..rules.faction_trigger_results import prepare_faction_derived_effect

        try:
            return True, prepare_faction_derived_effect(host, item, raw_effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.equipped_attack_die import DRAW_FIELD, ROLL_FIELD as EQUIPPED_ROLL_FIELD, bind_equipped_attack_die

    if EQUIPPED_ROLL_FIELD in raw_effect or DRAW_FIELD in raw_effect:
        try:
            return True, bind_equipped_attack_die(host, item, raw_effect, instruction_pointer=instruction_pointer)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..rules.die_roll_draw import ROLL_FIELD, bind_die_draw

    if ROLL_FIELD in raw_effect:
        try:
            return True, bind_die_draw(host, item, raw_effect, instruction_pointer=instruction_pointer)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
    if regeneration.is_controlled_creature_regeneration(raw_effect):
        return True, regeneration.bind_controlled_creature_regeneration(host, item, raw_effect)
    return False, None


def prepare_stack_resolution_effect(
    host, item, raw_effect: Mapping[str, Any], *, instruction_pointer: int = 0,
) -> dict[str, Any] | None:
    """Resolve one available instruction without changing its position in the program."""
    handled, bound = _prepare_source_bound_resolution(host, item, raw_effect, instruction_pointer)
    if handled:
        return bound
    from ..card_overrides import normalize_game_record_v3_effect
    from ..event_card_return import prepare_event_library_order
    from ..rules.casting.spell_text import cast_text_instruction_has_no_target
    from ..rules.entry_return_payment import ENTRY_RETURN_PAYMENT_VERSION_FIELD, bind_checked_entry_return_payment
    from ..scheduled_player import scheduled_player_instruction_is_impossible
    from ..rules.event_outcome_shapes import cast_event_token_recipient_has_left_game
    from ..end_combat_counterpart_model import END_COMBAT_COUNTERPART_FIELD

    from ..rules.post_exchange_token import POST_EXCHANGE_TOKEN_VERSION_FIELD, bind_post_exchange_token
    from ..fixed_effect_payment import EQUIPPED_DEATH_LIFE_DRAW_FIELD, bind_equipped_death_life_draw

    if EQUIPPED_DEATH_LIFE_DRAW_FIELD in raw_effect:
        try:
            return bind_equipped_death_life_draw(item, raw_effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if POST_EXCHANGE_TOKEN_VERSION_FIELD in raw_effect:
        try:
            return bind_post_exchange_token(host, item, raw_effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if END_COMBAT_COUNTERPART_FIELD in raw_effect:
        from ..end_combat_counterpart_runtime import bind_end_combat_counterpart_result

        try:
            return bind_end_combat_counterpart_result(host, item, raw_effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    if cast_event_token_recipient_has_left_game(host, item, raw_effect):
        return None
    if 'optional_target_result' in raw_effect:
        from ..rules.optional_target_results import bind_optional_target_result
        try:
            raw_effect = bind_optional_target_result(raw_effect, item.targets)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if raw_effect is None:return None

    if scheduled_player_instruction_is_impossible(host, item, raw_effect):
        host._log(
            item.controller, 'effect.player.skipped',
            f'Skipped a part of {item.ref} for a player who left the game.',
            {'stack': item.ref, 'operation': raw_effect.get('op'),
             'player': item.context['player'], 'reason': 'scheduled_player_departed'},
            importance=1,
        )
        return None

    scoped_item, raw_effect = _bind_event_resolution_results(host, item, raw_effect)
    if raw_effect is None:
        return None
    optional_usage, available = _optional_result_scope(host, scoped_item, raw_effect)
    if not available:
        return None
    from ..stack_counter import bind_conditional_stack_counter

    effect = normalize_game_record_v3_effect(host._semantic_value(raw_effect, scoped_item))
    from ..rules.attacking_entry_copy import materialize_copy, uses_copy

    if uses_copy(raw_effect):
        try:
            return materialize_copy(host, scoped_item, raw_effect, effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from .fixed_target_set_handlers import bind_revalidated_tap_target_cards

    effect = bind_revalidated_tap_target_cards(scoped_item, raw_effect, effect)
    from ..counter_placement_sets import bind_counter_receipt_source_exclusion
    from ..rules.affected_player_discard_capability_shapes import bind_equipped_damage_discard_player
    from ..rules.conditional_target_preparation import bind_resolution_target_color
    from ..rules.entry_x_support import entry_x_support_has_no_targets

    try:
        if entry_x_support_has_no_targets(scoped_item, raw_effect, effect):
            return None
        effect = bind_counter_receipt_source_exclusion(
            host, scoped_item, bind_equipped_damage_discard_player(host.state, raw_effect, effect),
        )
        if effect is not None:
            effect = bind_resolution_target_color(host, scoped_item, raw_effect, effect)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    if effect is None:
        return None
    from ..rules.temporary_occurrence_copy import TEMPORARY_OCCURRENCE_COPY_OPERATION, bind_temporary_occurrence_copy
    if effect.get("op") == TEMPORARY_OCCURRENCE_COPY_OPERATION:
        try:
            return bind_temporary_occurrence_copy(host, scoped_item, raw_effect, effect)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    from ..source_tap_effect import OPTIONAL_SOURCE_CONTROL_UNTAP_VERSION, SOURCE_CONTROL_UNTAP_FIELD

    if (effect.get("op") == "tap" and raw_effect.get(SOURCE_CONTROL_UNTAP_FIELD) == OPTIONAL_SOURCE_CONTROL_UNTAP_VERSION
            and effect.get("card") is None):
        return None
    effect = bind_conditional_stack_counter(host, scoped_item, raw_effect, effect)
    if effect is None:
        return None
    if ENTRY_RETURN_PAYMENT_VERSION_FIELD in raw_effect:
        effect = bind_checked_entry_return_payment(host, scoped_item, raw_effect, effect)
    if raw_effect.get('op') == 'offer_optional_effect':
        if optional_usage is not None and not _can_choose_limited_optional_result(host, effect):
            return None
        effect = _prepare_available_optional_effect(host, scoped_item, raw_effect, effect)
        if effect is None:
            return None
    if raw_effect.get("players") == "$targets":
        if effect.get("players") == []:
            return None
        amount_field = {"life": "delta", "choose_cards_apnap": "count"}.get(effect.get("op"))
        amount = raw_effect.get(amount_field)
        if isinstance(amount, Mapping) and type(effect.get(amount_field)) is int and effect[amount_field] == 0:
            from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND
            if amount.get("kind") == PUBLIC_QUERY_AMOUNT_KIND:
                return None
    if _is_zero_declared_result(effect, raw_effect, scoped_item):
        return None
    if effect.get("op") == "place_player_counters":
        from ..rules.unblocked_attackers import unblocked_poison_recipient_has_left
        if unblocked_poison_recipient_has_left(host, item, raw_effect, effect):
            return None
    if effect.get("op") == "prohibit_casting_until_end_of_turn":
        from ..rules.temporary_cast_restrictions import attacked_defender_cast_recipient_has_left

        if attacked_defender_cast_recipient_has_left(host, raw_effect, effect):
            return None
    if effect.get("op") == "choose_cards_apnap" and effect.get("then") in {"exile", "return_owner_hand"}:
        from ..rules.event_player_choice_shapes import event_bound_public_choice_recipient_has_left

        if event_bound_public_choice_recipient_has_left(host, raw_effect, effect):
            return None
    if effect.get("op") == "discard_random_cards":
        from ..random_discard import blocked_defender_discard_recipient_has_left

        if blocked_defender_discard_recipient_has_left(host, raw_effect, effect):
            return None
    if effect.get("op") == "mill":
        from ..milling import event_mill_recipient_has_left
        if event_mill_recipient_has_left(host, raw_effect, effect):
            return None
    if (any(key in effect and effect.get(key) is None for key in ('target', 'stack', 'card', 'object'))
        or scoped_item is not item and cast_text_instruction_has_no_target(raw_effect, effect)):
        host._log(
            item.controller, 'effect.target.skipped',
            f'Skipped a target-dependent part of {item.ref}.',
            {'stack': item.ref, 'operation': effect.get('op'), 'reason': 'that target is illegal'},
            importance=1,
        )
        return None
    from ..discard_effect_origin import bind_discard_effect_origin
    try:
        effect = bind_discard_effect_origin(host.state, scoped_item, effect)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
    return prepare_event_library_order(host, effect)


def execute_prepared_composition_movement(sink: SemanticIntentSink, plan: IntentPlan, prepared) -> object:
    if len(plan.intents) != 1 or not isinstance(plan.intents[0], MoveObjectsSimultaneouslyIntent):
        raise SemanticNodeError("Prepared physical departures require their one movement intent")
    intent, = plan.intents
    result = sink.move_objects_simultaneously_intent(intent, prepared_composition=prepared)
    _synchronize_instruction_observers(sink)
    return {intent.actor: result} if plan.result_shape == "by_player" else result


def execute_intent_plan(sink: SemanticIntentSink, plan: IntentPlan) -> object:
    if any(isinstance(intent, DrawCardsIntent) for intent in plan.intents):
        raise SemanticNodeError(
            "Draw intents require the replacement-aware draw coordinator"
        )
    results: list[tuple[str, object]] = []
    for intent in plan.intents:
        if isinstance(intent,CREATION_INTENT_TYPES):
            results.append(execute_creation_intent(sink,intent))
            continue
        if isinstance(intent,AscendIntent):
            from ..rules.city_blessing import award_citys_blessing
            results.append((intent.player,award_citys_blessing(sink,intent.player,reason=intent.reason)))
            continue
        if isinstance(intent, STACK_INTENT_TYPES):
            results.append(execute_stack_intent(sink, intent))
            continue
        if isinstance(intent, PLAYER_INTENT_TYPES):
            results.append(_execute_player_intent(sink, intent))
            continue
        if isinstance(intent, TAP_STATE_INTENT_TYPES):
            results.append(_execute_tap_state_intent(sink, intent))
            continue
        if isinstance(intent, UntapAllCreaturesIntent):
            result = tap_state.untap_all_creatures(
                sink,
                actor=intent.actor,
                reason=intent.reason,
            )
            results.append(("creatures", result))
            continue
        if isinstance(intent, PERMANENT_OBJECT_INTENT_TYPES):
            results.append(_execute_permanent_object_intent(sink, intent))
            continue
        if isinstance(intent, AddManaIntent):
            result = sink.apply_mana_intent(intent)
            results.append((intent.player, result))
            continue
        if isinstance(intent, SetCardDesignationIntent):
            result = sink.set_card_designation_intent(intent)
            results.append((intent.object_ref, result))
            continue
        if isinstance(intent, BecomeMonstrousIntent):
            result = sink.become_monstrous_intent(intent)
            results.append((intent.object_ref, result))
            continue
        if isinstance(intent, BecomeRenownedIntent):
            result = sink.become_renowned_intent(intent)
            results.append((intent.object_ref, result))
            continue
        if isinstance(intent, RECORDING_INTENT_TYPES):
            results.append(_execute_recording_intent(sink, intent))
            continue
        if isinstance(intent, ZoneMoveIntent):
            result = sink.move_object_intent(intent)
            results.append((intent.object_ref, result))
            continue
        if isinstance(intent, UnearthIntent):
            result = resolve_unearth_intent(sink, intent)
            results.append((intent.card_ref, result))
            continue
        if isinstance(intent, NinjutsuEntryIntent):
            result = resolve_ninjutsu_entry(sink, intent)
            results.append((intent.source_ref, result))
            continue
        if isinstance(intent, EncoreTokensIntent):
            result = resolve_encore_tokens(sink, intent)
            results.append((intent.source_ref, result))
            continue
        if isinstance(intent, SelfZoneMoveIntent):
            result = resolve_self_zone_move(sink, intent)
            results.append((intent.card_ref, result))
            continue
        if isinstance(intent,ExploitSacrificeIntent):
            result=sink.exploit_sacrifice_intent(intent)
            results.append((intent.actor,result))
            continue
        if isinstance(intent, MoveObjectsSimultaneouslyIntent):
            result = sink.move_objects_simultaneously_intent(intent)
            results.append((intent.actor, result))
            continue
        if isinstance(intent, (MadnessChoiceIntent,MiracleCastChoiceIntent,DiscoverCastIntent,DiscoverCleanupIntent,
                               GraveyardCastIntent,ExileGraveyardCopiesIntent,ResolvingExileLandPlayIntent,
                               RevealCapturedDrawsIntent,ResolveCapturedDrawCastIntent,ResolveBoundedHandCastIntent,
                               ResolveRippleCastIntent,RippleBottomIntent,ResolvingSharedTypeLibraryPlayIntent)):
            results.append(_execute_resolving_cast_choice(sink,intent))
            continue
        if isinstance(intent, LIBRARY_INTENT_TYPES):
            results.append(_execute_library_intent(sink, intent))
            continue
        if isinstance(intent, RecordZoneMoveIntent):
            sink.record_zone_move_intent(intent)
            results.append((intent.object_ref, None))
            continue
        if isinstance(intent, LIFE_INTENT_TYPES):
            results.append(execute_life_intent(sink,intent))
            continue
        if isinstance(intent, COUNTER_PLACEMENT_INTENT_TYPES):
            results.append(_execute_counter_placement_intent(sink, intent))
            continue
        if isinstance(intent, RemoveCountersIntent):
            result = execute_counter_removal(sink, intent)
            results.append((_COUNTER_RESULT_KEY, result))
            continue
        if isinstance(intent, COUNTER_MOVEMENT_INTENT_TYPES):
            from ..rules.counter_movement import execute_counter_movement
            results.append((_COUNTER_RESULT_KEY, execute_counter_movement(sink, intent)))
            continue
        if isinstance(intent, (RemoveAllCountersIntent, AllPermanentCountersThenTokenExileIntent)):
            result = sink.remove_all_counters_intent(intent)
            results.append((_COUNTER_RESULT_KEY, result))
            continue
        if isinstance(intent, (EliminatePlayersIntent, WinGameIntent)):
            sink.player_outcome_intent(intent)
            results.append(("players", None))
            continue
        if isinstance(intent, CreateTokenIntent):
            result = sink.create_token_intent(intent)
            results.append((intent.controller, result))
            continue
        if isinstance(intent, CopyControlledTokensIntent):
            result = sink.copy_controlled_tokens_intent(intent)
            results.append((intent.controller, result))
            continue
        if isinstance(intent, AddSubtypeIntent):
            result = sink.add_subtype_intent(intent)
            results.append((intent.object_ref, result))
            continue
        if isinstance(intent, GrantZoneObjectKeywordIntent):
            result = sink.grant_zone_object_keyword_intent(intent)
            results.append((intent.object_ref or intent.actor, result))
            continue
        if isinstance(intent, ProliferateIntent):
            result = sink.proliferate_intent(intent)
            results.append((intent.actor, result))
            continue
        if isinstance(intent, DealFixedDamageSetIntent):
            result = sink.deal_fixed_damage_set_intent(intent)
            results.append((intent.source_ref, result))
            continue
        if isinstance(intent, DomainEffectIntent):
            result = sink.apply_domain_effect_intent(intent)
            results.append((intent.operation, result))
            continue
        raise TypeError(f"Unsupported semantic intent {type(intent).__name__}")
    _synchronize_instruction_observers(sink)
    if plan.result_shape == "by_player":
        return dict(results)
    return results[0][1]
    SetCardDesignationIntent,
