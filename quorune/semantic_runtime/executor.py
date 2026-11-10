from __future__ import annotations
from .composition_intent import ResumeCompositionOrderIntent
from .mutate_intent import SelectMutateMergeIntent
from ..city_blessing_model import AscendIntent
from ..cipher_model import EncodeCipherIntent
from .soulbond_intent import PairSoulbondIntent
from ..discover_model import DiscoverCastIntent,DiscoverCleanupIntent
from ..targeted_graveyard_cast_model import GraveyardCastIntent
from ..rules.graveyard_copy_cast_model import ExileGraveyardCopiesIntent
from .life_batch_intent import LifeLossBatchIntent
from .counter_movement_intent import MoveCountersIntent
from .exploit_intent import ExploitSacrificeIntent
from .life_execution import LIFE_INTENT_TYPES,execute_life_intent
from .control_intents import GainControlIntent, GainControlSetIntent
from ..whole_hand_discard_model import DiscardWholeHandsIntent
from ..discard_result_model import DiscardWholeHandsResultIntent
from ..rules.discard_results import execute_discard_result
from .whole_hand_discard import execute_whole_hand_discard
from ..random_discard import RandomDiscardIntent
from .random_discard import execute_random_discard
from ..rules.random_graveyard_returns import RandomGraveyardReturnIntent
from .random_graveyard_returns import execute_random_graveyard_return
from ..temporary_cast_rule_model import TemporaryCastRestrictionIntent
from ..turn_skip_model import ScheduleTurnSkipIntent
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
    DealFixedDamageSetIntent,
    DomainEffectIntent,
    DestroyPermanentIntent,
    DestroyPermanentSetIntent,
    DestroyPermanentTargetsIntent,
    DrawCardsIntent,
    IntentPlan,
    EliminatePlayersIntent,
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
    ) -> tuple[str, ...]: ...

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

    def gain_control_intent(self, intent: GainControlIntent) -> object: ...

    def gain_control_set_intent(self, intent: GainControlSetIntent) -> object: ...

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
        intent: RemoveAllCountersIntent,
    ) -> Any: ...

    def counter_stack_intent(self, intent: CounterStackIntent) -> None: ...

    def eliminate_players_intent(self, intent: EliminatePlayersIntent) -> None: ...

    def copy_stack_item_intent(self, intent: CopyStackItemIntent) -> str: ...

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
    ) -> str: ...

    def proliferate_intent(self, intent: ProliferateIntent) -> None: ...

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
    PairSoulbondIntent | GainControlIntent | GainControlSetIntent | CreateRegenerationShieldIntent
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
    GainControlIntent, GainControlSetIntent,
    CreateRegenerationShieldIntent,
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
    if isinstance(intent, GainControlIntent):
        return intent.object_ref, sink.gain_control_intent(intent)
    if isinstance(intent, GainControlSetIntent):
        return intent.actor, sink.gain_control_set_intent(intent)
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
    if isinstance(intent, ExilePublicGraveyardCardIntent):
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


RecordingIntent = RecordChoiceIntent | ExploreCompletedIntent | ConniveCompletedIntent | InspectHandIntent
RECORDING_INTENT_TYPES = (
    RecordChoiceIntent,
    ExploreCompletedIntent,
    ConniveCompletedIntent,
    InspectHandIntent,
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
        return _COUNTER_RESULT_KEY, sink.place_counters_intent(intent)
    if isinstance(intent, PlaceCountersOnSetIntent):
        return _COUNTER_RESULT_KEY, sink.place_counters_on_set_intent(intent)
    if isinstance(intent, PlaceCountersOnTargetsIntent):
        return _COUNTER_RESULT_KEY, sink.place_counters_on_targets_intent(intent)
    return "player_counters", sink.place_player_counters_intent(intent)


PlayerIntent = (
    BecomeMonarchIntent | MillCardsIntent | ImpulseAccessIntent | DiscardWholeHandsIntent
    | DiscardWholeHandsResultIntent | RandomDiscardIntent | RandomGraveyardReturnIntent
    | TemporaryCastRestrictionIntent | ProhibitLibrarySearchIntent | ScheduleTurnSkipIntent
    | EarthbendIntent | FinishEarthbendIntent | ReturnEarthbentObjectsIntent | SuspectIntent
)
PLAYER_INTENT_TYPES = (
    BecomeMonarchIntent, MillCardsIntent, ImpulseAccessIntent, DiscardWholeHandsIntent,
    DiscardWholeHandsResultIntent, RandomDiscardIntent, RandomGraveyardReturnIntent,
    TemporaryCastRestrictionIntent, ProhibitLibrarySearchIntent, ScheduleTurnSkipIntent,
    EarthbendIntent, FinishEarthbendIntent, ReturnEarthbentObjectsIntent, SuspectIntent,
)
TapStateIntent = (
    SetPermanentTappedIntent
    | ProhibitNextUntapIntent
    | SetPermanentsTappedIntent
    | SetPublicPermanentsTappedIntent
)
TAP_STATE_INTENT_TYPES = (
    SetPermanentTappedIntent,
    ProhibitNextUntapIntent,
    SetPermanentsTappedIntent,
    SetPublicPermanentsTappedIntent,
)


def _execute_player_intent(
    sink: SemanticIntentSink,
    intent: PlayerIntent,
) -> tuple[str, object]:
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
    if isinstance(intent, ScheduleTurnSkipIntent):
        return intent.actor, tuple(sink.turn_steps.schedule_skip(seat, intent.spec) for seat in intent.players)
    if isinstance(intent,ProhibitLibrarySearchIntent):return intent.actor,apply_library_search_prohibition(sink,actor=intent.actor,source_id=intent.source_id)
    if isinstance(intent, RandomDiscardIntent):
        return intent.actor, execute_random_discard(sink, intent)
    if isinstance(intent, RandomGraveyardReturnIntent):
        return intent.actor, execute_random_graveyard_return(sink,intent)
    if isinstance(intent, TemporaryCastRestrictionIntent):
        return intent.actor,apply_temporary_cast_restriction(sink,actor=intent.actor,restriction=intent.restriction,
            target_player=intent.target_player,source_id=intent.source_id)
    if isinstance(intent, DiscardWholeHandsIntent):
        return intent.actor, execute_whole_hand_discard(sink, intent)
    if isinstance(intent, DiscardWholeHandsResultIntent):return intent.actor, execute_discard_result(sink, intent)
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
            ),
        )
        return intent.player, result.exiled_refs
    from ..milling import mill_cards, mill_player_batch, mill_request

    if intent.batch_players:
        results = mill_player_batch(sink, tuple(
            mill_request(sink, actor=intent.actor, player=player, count=intent.count, reason=intent.reason)
            for player in intent.batch_players
        ))
        return intent.actor, tuple(ref for result in results for ref in result.refs)

    result = mill_cards(
        sink,
        mill_request(
            sink, actor=intent.actor,
            player=intent.player,
            count=intent.count,
            reason=intent.reason,
        ),
    )
    return intent.player, result.refs


def _execute_tap_state_intent(
    sink: SemanticIntentSink,
    intent: TapStateIntent,
) -> tuple[str, object]:
    if isinstance(intent, ProhibitNextUntapIntent):
        return intent.object_ref, tap_state.prohibit_next_untap(sink, intent.object_ref,
            actor=intent.actor, reason=intent.reason, logical_object_id=intent.logical_object_id)
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


def _can_choose_limited_optional_result(host, effect: Mapping[str, Any]) -> bool:
    child, = effect['effects']
    actor = effect['player']
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


def prepare_stack_resolution_effect(host, item, raw_effect: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve one available instruction without changing its position in the program."""
    from ..card_overrides import normalize_game_record_v3_effect
    from ..rules.casting.spell_text import cast_text_instruction_has_no_target, scoped_cast_text_instruction
    from ..rules.entry_return_payment import ENTRY_RETURN_PAYMENT_VERSION_FIELD, bind_checked_entry_return_payment
    from ..scheduled_player import scheduled_player_instruction_is_impossible

    if scheduled_player_instruction_is_impossible(host, item, raw_effect):
        host._log(
            item.controller, 'effect.player.skipped',
            f'Skipped a part of {item.ref} for a player who left the game.',
            {'stack': item.ref, 'operation': raw_effect.get('op'),
             'player': item.context['player'], 'reason': 'scheduled_player_departed'},
            importance=1,
        )
        return None

    try:
        scoped_item, raw_effect = scoped_cast_text_instruction(item, raw_effect)
    except ValueError as exc:
        raise SemanticNodeError(str(exc)) from exc
    optional_usage = None
    if raw_effect.get('op') == 'offer_optional_effect':
        from ..rules.trigger_limits import optional_result_usage_available, optional_result_usage_binding

        try:
            optional_usage = optional_result_usage_binding(host, scoped_item)
            if optional_usage is not None and not optional_result_usage_available(host.state, optional_usage):
                return None
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    effect = normalize_game_record_v3_effect(host._semantic_value(raw_effect, scoped_item))
    if ENTRY_RETURN_PAYMENT_VERSION_FIELD in raw_effect:
        effect = bind_checked_entry_return_payment(host, scoped_item, raw_effect, effect)
    if optional_usage is not None and not _can_choose_limited_optional_result(host, effect):
        return None
    if raw_effect.get('op') == 'offer_optional_effect':
        from ..rules.attachment_actions import unavailable_optional_entry_equipment_attachment

        if unavailable_optional_combat_removal(host, raw_effect, effect):
            return None
        try:
            if unavailable_optional_entry_equipment_attachment(host, raw_effect, effect):
                return None
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
    if raw_effect.get("players") == "$targets":
        if effect.get("players") == []:
            return None
        amount_field = {"life": "delta", "choose_cards_apnap": "count"}.get(effect.get("op"))
        amount = raw_effect.get(amount_field)
        if isinstance(amount, Mapping) and type(effect.get(amount_field)) is int and effect[amount_field] == 0:
            from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND
            if amount.get("kind") == PUBLIC_QUERY_AMOUNT_KIND:
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
    return effect


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
                               GraveyardCastIntent,ExileGraveyardCopiesIntent)):
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
            result = sink.remove_counters_intent(intent)
            results.append((_COUNTER_RESULT_KEY, result))
            continue
        if isinstance(intent, MoveCountersIntent):
            from ..rules.counter_movement import execute_counter_movement
            results.append((_COUNTER_RESULT_KEY, execute_counter_movement(sink, intent)))
            continue
        if isinstance(intent, RemoveAllCountersIntent):
            result = sink.remove_all_counters_intent(intent)
            results.append((_COUNTER_RESULT_KEY, result))
            continue
        if isinstance(intent, EliminatePlayersIntent):
            sink.eliminate_players_intent(intent)
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
            results.append((intent.object_ref, result))
            continue
        if isinstance(intent, ProliferateIntent):
            sink.proliferate_intent(intent)
            results.append((intent.actor, None))
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
