from __future__ import annotations

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
from ..self_zone_move import resolve_self_zone_move, SelfZoneMoveIntent
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
    CreateRegenerationShieldIntent,
    DealFixedDamageSetIntent,
    DomainEffectIntent,
    DestroyPermanentIntent,
    DestroyPermanentSetIntent,
    DrawCardsIntent,
    IntentPlan,
    EliminatePlayersIntent,
    ExploreCompletedIntent,
    ExilePermanentIntent,
    ExilePublicGraveyardCardIntent,
    GrantZoneObjectKeywordIntent,
    LifeChangeIntent,
    MoveObjectsSimultaneouslyIntent,
    MovePublicZoneSetIntent,
    MoveLibraryCardsToBottomIntent,
    MillCardsIntent,
    ImpulseAccessIntent,
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
    SetCardDesignationIntent,
    SetPermanentTappedIntent,
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

    def move_object_intent(self, intent: ZoneMoveIntent) -> str: ...

    def move_objects_simultaneously_intent(
        self,
        intent: MoveObjectsSimultaneouslyIntent,
    ) -> tuple[str, ...]: ...

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

    def pay_life_intent(self, intent: PayLifeIntent) -> int: ...

    def reveal_library_cards_intent(
        self,
        intent: RevealLibraryCardsIntent,
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

    def reorder_library_top_intent(
        self,
        intent: ReorderLibraryTopIntent,
    ) -> tuple[str, ...]: ...

    def pay_mana_cost_intent(self, intent: PayManaCostIntent) -> None: ...

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
    CreateRegenerationShieldIntent
    | DestroyPermanentIntent
    | DestroyPermanentSetIntent
    | ExilePermanentIntent
    | ExilePublicGraveyardCardIntent
    | MovePublicZoneSetIntent
    | ReturnPermanentToOwnerHandIntent
    | ReturnGraveyardCardToOwnerHandIntent
)
PERMANENT_OBJECT_INTENT_TYPES = (
    CreateRegenerationShieldIntent,
    DestroyPermanentIntent,
    DestroyPermanentSetIntent,
    ExilePermanentIntent,
    ExilePublicGraveyardCardIntent,
    MovePublicZoneSetIntent,
    ReturnPermanentToOwnerHandIntent,
    ReturnGraveyardCardToOwnerHandIntent,
)


def _execute_permanent_object_intent(
    sink: SemanticIntentSink,
    intent: PermanentObjectIntent,
) -> tuple[str, object]:
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
                replacement_selections=intent.replacement_selections,
            ),
        )
    if isinstance(intent, DestroyPermanentSetIntent):
        return intent.actor, sink.destroy_permanent_set_intent(intent)
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
        ),
    )


RecordingIntent = RecordChoiceIntent | ExploreCompletedIntent
RECORDING_INTENT_TYPES = (RecordChoiceIntent, ExploreCompletedIntent)


def _execute_recording_intent(
    sink: SemanticIntentSink,
    intent: RecordingIntent,
) -> tuple[str, None]:
    if isinstance(intent, RecordChoiceIntent):
        sink.record_choice_intent(intent)
        return intent.actor, None
    sink.complete_explore_intent(intent)
    return intent.explorer_ref, None


LibraryIntent = (
    ChooseOneRestBottomRandomIntent
    | ShuffleLibraryIntent
    | ReturnCardsToLibraryTopIntent
    | RevealLibraryCardsIntent
    | MoveLibraryCardsToBottomIntent
    | ScryLibraryIntent
    | SurveilLibraryIntent
    | ReorderLibraryTopIntent
)
LIBRARY_INTENT_TYPES = (
    ChooseOneRestBottomRandomIntent,
    ShuffleLibraryIntent,
    ReturnCardsToLibraryTopIntent,
    RevealLibraryCardsIntent,
    MoveLibraryCardsToBottomIntent,
    ScryLibraryIntent,
    SurveilLibraryIntent,
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
    if isinstance(intent, MoveLibraryCardsToBottomIntent):
        return intent.player, sink.move_library_cards_to_bottom_intent(intent)
    if isinstance(intent, ScryLibraryIntent):
        return intent.player, sink.scry_library_intent(intent)
    if isinstance(intent, SurveilLibraryIntent):
        return intent.player, sink.surveil_library_intent(intent)
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


PlayerIntent = BecomeMonarchIntent | MillCardsIntent | ImpulseAccessIntent
PLAYER_INTENT_TYPES = (BecomeMonarchIntent, MillCardsIntent, ImpulseAccessIntent)


def _execute_player_intent(
    sink: SemanticIntentSink,
    intent: PlayerIntent,
) -> tuple[str, object]:
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
    from ..milling import MillRequest, mill_cards

    result = mill_cards(
        sink,
        MillRequest(
            actor=intent.actor,
            player=intent.player,
            count=intent.count,
            reason=intent.reason,
        ),
    )
    return intent.player, result.refs


def execute_intent_plan(sink: SemanticIntentSink, plan: IntentPlan) -> object:
    if any(isinstance(intent, DrawCardsIntent) for intent in plan.intents):
        raise SemanticNodeError(
            "Draw intents require the replacement-aware draw coordinator"
        )
    results: list[tuple[str, object]] = []
    for intent in plan.intents:
        if isinstance(intent, PLAYER_INTENT_TYPES):
            results.append(_execute_player_intent(sink, intent))
            continue
        if isinstance(intent, SetPermanentTappedIntent):
            result = tap_state.set_permanent_tapped(
                sink,
                intent.object_ref,
                actor=intent.actor,
                tapped=intent.tapped,
                reason=intent.reason,
                logical_object_id=intent.logical_object_id,
            )
            results.append((intent.object_ref, result))
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
        if isinstance(intent, SelfZoneMoveIntent):
            result = resolve_self_zone_move(sink, intent)
            results.append((intent.card_ref, result))
            continue
        if isinstance(intent, MoveObjectsSimultaneouslyIntent):
            result = sink.move_objects_simultaneously_intent(intent)
            results.append((intent.actor, result))
            continue
        if isinstance(intent, LIBRARY_INTENT_TYPES):
            results.append(_execute_library_intent(sink, intent))
            continue
        if isinstance(intent, RecordZoneMoveIntent):
            sink.record_zone_move_intent(intent)
            results.append((intent.object_ref, None))
            continue
        if isinstance(intent, LifeChangeIntent):
            result = sink.apply_life_change_intent(intent)
            results.append((intent.player, result))
            continue
        if isinstance(intent, PayLifeIntent):
            result = sink.pay_life_intent(intent)
            results.append((intent.player, result))
            continue
        if isinstance(intent, PayManaCostIntent):
            sink.pay_mana_cost_intent(intent)
            results.append((intent.player, None))
            continue
        if isinstance(intent, COUNTER_PLACEMENT_INTENT_TYPES):
            results.append(_execute_counter_placement_intent(sink, intent))
            continue
        if isinstance(intent, RemoveCountersIntent):
            result = sink.remove_counters_intent(intent)
            results.append((_COUNTER_RESULT_KEY, result))
            continue
        if isinstance(intent, RemoveAllCountersIntent):
            result = sink.remove_all_counters_intent(intent)
            results.append((_COUNTER_RESULT_KEY, result))
            continue
        if isinstance(intent, CounterStackIntent):
            sink.counter_stack_intent(intent)
            results.append((intent.stack_ref, None))
            continue
        if isinstance(intent, EliminatePlayersIntent):
            sink.eliminate_players_intent(intent)
            results.append(("players", None))
            continue
        if isinstance(intent, CopyStackItemIntent):
            result = sink.copy_stack_item_intent(intent)
            results.append((intent.target_stack_ref, result))
            continue
        if isinstance(intent, RetargetStackItemIntent):
            result = sink.retarget_stack_item_intent(intent)
            results.append((intent.target_stack_ref, result))
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
    if plan.result_shape == "by_player":
        return dict(results)
    return results[0][1]
    SetCardDesignationIntent,
