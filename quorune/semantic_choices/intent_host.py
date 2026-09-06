from __future__ import annotations

import copy
import random
from typing import Any

from ..counter_placement import (
    CounterPlacementError,
    CounterPlacementRequest,
    place_counters,
    place_counters_on_refs,
)
from ..counter_placement_sets import (
    CounterPlacementSetError,
    resolve_counter_placement_set,
)
from ..counter_placement_targets import (
    CounterPlacementTargetSetError,
    resolve_counter_placement_targets,
)
from ..counter_removal import (
    commit_all_counter_removal_effect,
    commit_counter_removal_effect,
    AllCounterRemovalResult,
    CounterRemoval,
    CounterRemovalError,
    CounterRemovalResult,
    plan_all_counter_removal_effect,
    plan_counter_removal_effect,
)
from ..counter_state import player_counter_snapshot
from ..continuous_effect_state import ResolutionEffectSource
from ..errors import GameRuleError
from ..trigger_processing import schedule_delayed_trigger
from ..fixed_damage_set import (
    FixedDamageSetError,
    resolve_fixed_damage_set,
)
from ..destruction_sets import (
    DestructionSetError,
    resolve_destruction_set,
)
from ..effect_runtime import dispatch_effect
from ..life_change import (
    commit_life_change_batch,
    LifeChangeError,
    LifeChangeRequest,
    prepare_life_change_batch,
)
from ..life_state import LifeStateError, pay_life_cost
from ..player_result_events import dispatch_life_gain_records
from ..permanent_designations import (
    BecomeMonstrousRequest,
    BecomeRenownedRequest,
    PermanentDesignationError,
    become_monstrous,
    become_renowned,
)
from ..rules.library_scry import ScryError, commit_scry_arrangement
from ..rules.library_selection import (
    LibrarySelectionError,
    commit_library_selection,
)
from ..rules.library_surveillance import (
    SurveilError,
    commit_surveil_arrangement,
)
from ..replacement.immutable import thaw_value
from ..replacement import ReplacementChoiceRequired, ReplacementEventBatch
from ..semantic_runtime import (
    AddManaIntent,
    AddSubtypeIntent,
    BecomeMonstrousIntent,
    BecomeRenownedIntent,
    ChooseOneRestBottomRandomIntent,
    CopyControlledTokensIntent,
    CopyStackItemIntent,
    CounterStackIntent,
    DealFixedDamageSetIntent,
    DestroyPermanentSetIntent,
    CreateTokenIntent,
    DomainEffectIntent,
    EliminatePlayersIntent,
    ExploreCompletedIntent,
    GrantZoneObjectKeywordIntent,
    LifeChangeIntent,
    LibrarySelectionIntent,
    MoveLibraryCardsToBottomIntent,
    ScryLibraryIntent,
    SurveilLibraryIntent,
    MoveObjectsSimultaneouslyIntent,
    MovePublicZoneSetIntent,
    PayLifeIntent,
    PayManaCostIntent,
    PlaceCounterBatchIntent,
    PlaceCountersIntent,
    PlaceCountersOnSetIntent,
    PlaceCountersOnTargetsIntent,
    PlacePlayerCountersIntent,
    RemoveAllCountersIntent,
    RemoveCountersIntent,
    ProliferateIntent,
    RecordChoiceIntent,
    RecordZoneMoveIntent,
    ReorderLibraryTopIntent,
    RetargetStackItemIntent,
    ReturnCardsToLibraryTopIntent,
    RevealLibraryCardsIntent,
    SetCardDesignationIntent,
    ShuffleLibraryIntent,
    ZoneMoveIntent,
)
from ..zone_object_keyword_grants import (
    ZoneObjectKeywordGrantError,
    commit_zone_object_keyword_grant,
)
from ..public_zone_moves import (
    PublicZoneMoveError,
    resolve_public_zone_move_set,
)
from ..zone_object_subtype_grants import (
    ZoneObjectSubtypeGrantError,
    commit_zone_object_subtype_addition,
)
from ..semantic_runtime.zone_replacements import (
    prepare_zone_change_replacement,
)
from ..semantic_runtime.life_replacements import (
    collect_life_change_replacement_effects,
)


_REASON_FIELD = "reason"
from ..util import unique_preserving_order


class SemanticChoiceIntentHostMixin:
    def affected_permanent_active_seats(self) -> tuple[str, ...]:
        return tuple(self.active_seats)

    def affected_permanent_apnap_order(self) -> tuple[str, ...]:
        return tuple(self.apnap_order())

    def affected_permanent_object_rows(self, actor: str):
        return tuple(self._semantic_choice_object_rows(actor))

    def public_zone_move_active_seats(self) -> tuple[str, ...]:
        return tuple(self.active_seats)

    def public_zone_move_apnap_order(self) -> tuple[str, ...]:
        return tuple(self.apnap_order())

    def public_zone_move_object_rows(self, actor: str):
        return tuple(self._semantic_choice_object_rows(actor))

    def counter_target_active_seats(self) -> tuple[str, ...]:
        return tuple(self.active_seats)

    def counter_target_apnap_order(self) -> tuple[str, ...]:
        return tuple(self.apnap_order())

    def counter_target_object_rows(
        self,
        actor: str,
        refs: tuple[str, ...],
    ):
        by_ref = {
            row.ref: row
            for row in self._semantic_choice_object_rows(actor)
            if row.zone == "battlefield" and not row.phased_out
        }
        return tuple(by_ref[ref] for ref in refs if ref in by_ref)

    def fixed_damage_active_seats(self) -> tuple[str, ...]:
        return tuple(self.active_seats)

    def fixed_damage_apnap_order(self) -> tuple[str, ...]:
        return tuple(self.apnap_order())

    def fixed_damage_object_rows(self, actor: str):
        return tuple(self._semantic_choice_object_rows(actor))

    def deal_fixed_damage_set_intent(
        self,
        intent: DealFixedDamageSetIntent,
    ):
        try:
            return resolve_fixed_damage_set(
                self,
                actor=intent.actor,
                source_ref=intent.source_ref,
                amount=intent.amount,
                spec=intent.spec,
                reason=intent.reason,
                replacement_selections=tuple(
                    thaw_value(value)
                    for value in intent.replacement_selections
                ),
                replacement_event_ids=intent.replacement_event_ids,
            )
        except FixedDamageSetError as exc:
            raise GameRuleError(str(exc)) from exc

    def destroy_permanent_set_intent(
        self,
        intent: DestroyPermanentSetIntent,
    ):
        try:
            return resolve_destruction_set(
                self,
                actor=intent.actor,
                spec=intent.spec,
                reason=intent.reason,
                source_ref=intent.source_ref,
                regeneration_prohibited=intent.regeneration_prohibited,
                replacement_selections=tuple(
                    thaw_value(value)
                    for value in intent.replacement_selections
                ),
            )
        except DestructionSetError as exc:
            raise GameRuleError(str(exc)) from exc

    def move_public_zone_set_intent(
        self,
        intent: MovePublicZoneSetIntent,
    ):
        try:
            return resolve_public_zone_move_set(
                self,
                actor=intent.actor,
                spec=intent.spec,
                reason=intent.reason,
                source_ref=intent.source_ref,
                replacement_selections=tuple(
                    thaw_value(value)
                    for value in intent.replacement_selections
                ),
            )
        except PublicZoneMoveError as exc:
            raise GameRuleError(str(exc)) from exc

    def apply_domain_effect_intent(self, intent: DomainEffectIntent) -> Any:
        try:
            effect = thaw_value(intent.effect)
            return dispatch_effect(
                self,
                effect,
                actor=intent.actor,
                operation=intent.operation,
                reason=intent.reason,
            )
        except KeyError as exc:
            raise GameRuleError(
                f"Malformed {intent.operation!r} effect: missing {exc.args[0]!r}"
            ) from exc

    def apply_mana_intent(self, intent: AddManaIntent) -> int:
        return int(
            dispatch_effect(
                self,
                {
                    "op": "mana",
                    "player": intent.player,
                    "color": intent.color,
                    "amount": intent.amount,
                    "source": intent.source_ref,
                },
                actor=intent.actor,
                operation="mana",
                reason=intent.reason,
            )
        )

    def set_card_designation_intent(
        self,
        intent: SetCardDesignationIntent,
    ) -> str:
        if (
            intent.apply_as_subtype
            and intent.designation != "chosen_creature_type"
        ):
            raise GameRuleError(
                "Only a chosen creature type may become a subtype"
            )
        card = self._resolve_object(intent.actor, intent.object_ref)
        annotation_key = intent.designation
        card.annotations[annotation_key] = intent.value
        if intent.apply_as_subtype:
            card.annotations["chosen_creature_type_adds_subtype"] = True
        event_code = (
            "card.name.chosen"
            if annotation_key == "chosen_name"
            else "creature_type.chosen"
        )
        detail_key = (
            "card_name"
            if annotation_key == "chosen_name"
            else "creature_type"
        )
        self._log(
            intent.actor,
            event_code,
            f"{intent.actor} chose {intent.value} for {card.ref}.",
            {
                "source": card.ref,
                detail_key: intent.value,
                "reason": intent.reason,
            },
            importance=2,
            changed_objects=[card.object_id],
        )
        return intent.value

    def become_monstrous_intent(
        self,
        intent: BecomeMonstrousIntent,
    ):
        try:
            return become_monstrous(
                self,
                BecomeMonstrousRequest(
                    actor=intent.actor,
                    object_id=intent.object_id,
                    object_ref=intent.object_ref,
                    logical_object_id=intent.logical_object_id,
                    value=intent.value,
                    reason=intent.reason,
                ),
            )
        except PermanentDesignationError as exc:
            raise GameRuleError(str(exc)) from exc

    def become_renowned_intent(
        self,
        intent: BecomeRenownedIntent,
    ):
        try:
            return become_renowned(
                self,
                BecomeRenownedRequest(
                    actor=intent.actor,
                    object_id=intent.object_id,
                    object_ref=intent.object_ref,
                    logical_object_id=intent.logical_object_id,
                    reason=intent.reason,
                ),
            )
        except PermanentDesignationError as exc:
            raise GameRuleError(str(exc)) from exc

    def record_choice_intent(self, intent: RecordChoiceIntent) -> None:
        changed_objects = [
            card.object_id
            for ref in intent.changed_object_refs
            for card in [
                next(
                    (
                        candidate
                        for candidate in self.state.cards.values()
                        if candidate.ref == ref
                    ),
                    None,
                )
            ]
            if card is not None
        ]
        self._log(
            intent.actor,
            intent.event_code,
            intent.message,
            dict(intent.details),
            importance=intent.importance,
            visibility=intent.visibility,
            changed_objects=changed_objects,
            changed_players=intent.changed_players,
        )

    def complete_explore_intent(self, intent: ExploreCompletedIntent) -> None:
        explorer = next(
            (
                card
                for card in self.state.cards.values()
                if card.ref == intent.explorer_ref
            ),
            None,
        )
        changed_objects = (
            [explorer.object_id]
            if explorer is not None
            and explorer.logical_object_id
            == intent.explorer_logical_object_id
            else []
        )
        details = {
            "player": intent.player,
            "explorer": intent.explorer_ref,
            "explorer_logical_object_id": (
                intent.explorer_logical_object_id
            ),
            "result": intent.result,
            "reason": intent.reason,
        }
        if intent.revealed_card_ref is not None:
            details["revealed_card"] = intent.revealed_card_ref
        self._log(
            intent.actor,
            "explore.complete",
            f"{intent.explorer_ref} explored for {intent.player}.",
            details,
            importance=2,
            changed_objects=changed_objects,
            changed_players=[intent.player],
        )
        self._dispatch_semantic_event(
            "permanent.explores",
            details,
        )

    def move_object_intent(self, intent: ZoneMoveIntent) -> str:
        try:
            card = self._resolve_object(
                intent.actor,
                intent.object_ref,
                zones=set(intent.expected_zones),
                owned_only=intent.owned_only,
                controlled_only=intent.controlled_only,
            )
        except GameRuleError:
            if intent.optional_if_missing:
                return ""
            raise
        if (
            intent.expected_zone_change_counter is not None
            and card.zone_change_counter
            != intent.expected_zone_change_counter
        ):
            if intent.optional_if_missing:
                return ""
            raise GameRuleError(
                "The selected object is no longer the expected zone incarnation"
            )
        types, _, _ = self._type_parts(
            str(self._effective_card_data(card).get("type_line") or "")
        )
        if any(required not in types for required in intent.required_types):
            raise GameRuleError(
                "The selected object no longer has the required card type"
            )
        tapped: bool | None = None
        if intent.tapped_policy == "land_entry":
            record = self.card_record(card)
            if record is None or not record.is_land:
                raise GameRuleError("Land-entry movement requires a land card")
        elif intent.tapped_policy == "tapped":
            tapped = True
        elif intent.tapped_policy == "untapped":
            tapped = False
        replacement_selections = tuple(
            thaw_value(value) for value in intent.replacement_selections
        )
        prepared_replacement = None
        if intent.effect_entry_counters:
            prepared_replacement = prepare_zone_change_replacement(
                self,
                card,
                intent.destination,
                destination_controller=intent.new_controller,
                effect_entry_counters=intent.effect_entry_counters,
                selections=replacement_selections,
                error_type=GameRuleError,
            )
            replacement_selections = ()
        self.move_card(
            card.object_id,
            intent.destination,
            controller=intent.new_controller,
            tapped=tapped,
            reason=intent.reason,
            semantic_events=intent.semantic_events,
            replacement_selections=replacement_selections,
            prepared_replacement=prepared_replacement,
        )
        return card.ref

    def move_objects_simultaneously_intent(
        self,
        intent: MoveObjectsSimultaneouslyIntent,
    ) -> tuple[str, ...]:
        cards = tuple(
            self._resolve_object(
                intent.actor,
                ref,
                zones=set(intent.expected_zones),
                owned_only=intent.owned_only,
                controlled_only=intent.controlled_only,
            )
            for ref in intent.object_refs
        )
        self._move_cards_simultaneously(
            [
                (card.object_id, intent.destination)
                for card in cards
            ],
            reason=intent.reason,
            log=False,
            replacement_selections=intent.replacement_selections,
            transition_kinds={
                card.object_id: intent.transition_kind
                for card in cards
            },
        )
        return tuple(card.ref for card in cards)

    def choose_one_rest_bottom_random_intent(
        self,
        intent: ChooseOneRestBottomRandomIntent,
    ) -> tuple[str, ...]:
        if intent.chosen_ref not in intent.looked_refs:
            raise GameRuleError("Chosen card was not in the looked-at set")
        library = self.state.players[intent.player].zones["library"]
        cards = tuple(
            self._resolve_object(
                intent.actor,
                ref,
                zones={"library"},
                owned_only=True,
            )
            for ref in intent.looked_refs
        )
        if any(card.object_id not in library for card in cards):
            raise GameRuleError("A looked-at card left the library")
        chosen = next(
            card for card in cards if card.ref == intent.chosen_ref
        )
        for card in cards:
            if card is not chosen:
                library.remove(card.object_id)
        self.move_card(
            chosen.object_id,
            "hand",
            reason=intent.reason,
            log=False,
        )
        bottom = [
            card.object_id for card in cards if card is not chosen
        ]
        random.Random(
            f"{self.state.config.seed}|{intent.player}|fomori-vault|"
            f"{self.state.event_sequence}|{intent.source_stack_ref}"
        ).shuffle(bottom)
        library[0:0] = bottom
        for object_id in bottom:
            card = self.state.cards[object_id]
            card.known_to = [intent.player]
            card.revealed_to = []
        self._log(
            intent.actor,
            intent.event_code,
            (
                f"{intent.player} put one looked-at card into hand and "
                f"{len(bottom)} on the bottom at random."
            ),
            {
                "chosen": chosen.ref,
                "bottom_count": len(bottom),
                "looked_count": len(cards),
            },
            visibility=[intent.player, "analyst"],
            importance=2,
            changed_objects=[card.object_id for card in cards],
            changed_players=[intent.player],
        )
        return tuple(card.ref for card in cards)

    def shuffle_library_intent(self, intent: ShuffleLibraryIntent) -> None:
        self.shuffle_library(intent.player, reason=intent.reason)

    def return_cards_to_library_top_intent(
        self,
        intent: ReturnCardsToLibraryTopIntent,
    ) -> tuple[str, ...]:
        cards = [
            self._resolve_object(
                intent.actor,
                ref,
                zones={"hand"},
                owned_only=True,
            )
            for ref in intent.refs_top_first
        ]
        for card in reversed(cards):
            self.move_card(
                card.object_id,
                "library",
                position="top",
                reason=intent.reason,
                log=False,
            )
        return tuple(card.ref for card in cards)

    def record_zone_move_intent(self, intent: RecordZoneMoveIntent) -> None:
        card = self._resolve_object(intent.actor, intent.object_ref)
        details = dict(intent.details)
        if details.pop("include_tapped_state", False):
            details["tapped"] = card.tapped
        self._log(
            intent.actor,
            intent.event_code,
            intent.message,
            details,
            importance=intent.importance,
            changed_objects=[card.object_id],
            changed_players=(
                [intent.changed_player]
                if intent.changed_player is not None
                else []
            ),
        )

    def apply_life_change_intent(self, intent: LifeChangeIntent) -> int:
        try:
            prepared = prepare_life_change_batch(
                self,
                (
                    LifeChangeRequest(
                        event_id=(
                            f"semantic.life:{self.state.revision}:"
                            f"{self.state.event_sequence + 1}"
                        ),
                        player=intent.player,
                        amount=intent.amount,
                        source=intent.source_ref,
                        source_controller=intent.actor,
                        cause=intent.reason,
                    ),
                ),
                effects=collect_life_change_replacement_effects(self),
                selections=intent.replacement_selections,
                require_all_selections=False,
                batch_id=(
                    f"replacement:semantic.life:{self.state.revision}:"
                    f"{self.state.event_sequence + 1}"
                ),
            )
            if prepared.pending is not None:
                raise ReplacementChoiceRequired(
                    batch=ReplacementEventBatch(
                        batch_id=prepared.batch_id,
                        events=prepared.events,
                        apnap_order=tuple(self.apnap_order()),
                        journal=prepared.journal,
                    ),
                    effects=prepared.effects,
                    pending=prepared.pending,
                )
            committed = commit_life_change_batch(self, prepared)
        except LifeChangeError as exc:
            raise GameRuleError(str(exc)) from exc
        record = committed.records[0]
        self._log(
            intent.actor,
            "effect.life",
            f"{intent.player}'s life changed by {record.delta}.",
            {
                "player": intent.player,
                "requested_delta": intent.amount,
                "delta": record.delta,
                "reason": intent.reason,
                "source": intent.source_ref,
            },
            importance=1,
            changed_players=[intent.player],
        )
        dispatch_life_gain_records(self, committed.records)
        return self.state.players[intent.player].life

    def pay_life_intent(self, intent: PayLifeIntent) -> int:
        try:
            transition = pay_life_cost(
                self,
                intent.player,
                intent.amount,
            )
        except LifeStateError as exc:
            raise GameRuleError(str(exc)) from exc
        self._log(
            intent.actor,
            "life.pay",
            f"{intent.player} paid {intent.amount} life.",
            {
                "player": intent.player,
                "amount": intent.amount,
                "reason": intent.reason,
            },
            importance=1,
            changed_players=[intent.player],
        )
        return transition.after

    def reveal_library_cards_intent(
        self,
        intent: RevealLibraryCardsIntent,
    ) -> tuple[str, ...]:
        self._require_seat(intent.player, in_game=True)
        self._require_seat(intent.viewer, in_game=True)
        library = self.state.players[intent.player].zones["library"]
        refs = tuple(intent.refs_top_first)
        current = tuple(
            self.state.cards[object_id].ref
            for object_id in reversed(library[-len(refs) :])
        ) if refs else ()
        if current != refs:
            raise GameRuleError("The inspected library top changed")
        object_ids: list[str] = []
        for ref in refs:
            card = self._resolve_object(
                intent.viewer,
                ref,
                zones={"library"},
                owned_only=(intent.viewer == intent.player),
            )
            viewers = set(self.seats) if intent.public else {intent.viewer}
            card.known_to = sorted(set(card.known_to).union(viewers))
            if intent.public:
                card.revealed_to = sorted(
                    set(card.revealed_to).union(viewers)
                )
            object_ids.append(card.object_id)
        self._log(
            intent.actor,
            "library.look",
            (
                f"{intent.viewer} looked at the top {len(refs)} card(s) "
                f"of {intent.player}'s library."
            ),
            {"player": intent.player, "count": len(refs)},
            visibility=(
                None
                if intent.public
                else [intent.viewer, "analyst"]
            ),
            importance=1,
            changed_objects=object_ids,
        )
        return refs

    def move_library_cards_to_bottom_intent(
        self,
        intent: MoveLibraryCardsToBottomIntent,
    ) -> tuple[str, ...]:
        library = self.state.players[intent.player].zones["library"]
        object_ids: list[str] = []
        for ref in intent.refs:
            card = self._resolve_object(
                intent.actor,
                ref,
                zones={"library"},
                owned_only=True,
            )
            if card.object_id not in library:
                raise GameRuleError(
                    "A scry card left the library before the choice"
                )
            library.remove(card.object_id)
            object_ids.append(card.object_id)
        for object_id in reversed(object_ids):
            library.insert(0, object_id)
        self._log(
            intent.actor,
            "library.scry",
            f"{intent.actor} put {len(object_ids)} card(s) on the bottom.",
            {
                "count": intent.looked_count,
                "bottom_count": len(object_ids),
            },
            visibility=[intent.actor, "analyst"],
            importance=1,
            changed_objects=object_ids,
        )
        return intent.refs

    def scry_library_intent(
        self,
        intent: ScryLibraryIntent,
    ) -> tuple[str, ...]:
        try:
            return commit_scry_arrangement(
                self,
                actor=intent.actor,
                player=intent.player,
                arrangement=intent.arrangement,
                reason=intent.reason,
            )
        except ScryError as exc:
            raise GameRuleError(str(exc)) from exc

    def surveil_library_intent(
        self,
        intent: SurveilLibraryIntent,
    ) -> object:
        try:
            return commit_surveil_arrangement(
                self,
                actor=intent.actor,
                player=intent.player,
                arrangement=intent.arrangement,
                requested_count=intent.requested_count,
                reason=intent.reason,
                replacement_selections=intent.replacement_selections,
            )
        except SurveilError as exc:
            raise GameRuleError(str(exc)) from exc

    def library_selection_intent(
        self,
        intent: LibrarySelectionIntent,
    ) -> tuple[str, ...]:
        try:
            looked = commit_library_selection(
                self,
                actor=intent.actor,
                player=intent.player,
                arrangement=intent.arrangement,
                reason=intent.reason,
                source_stack_ref=intent.source_stack_ref,
                looked_are_public=intent.looked_are_public,
                selected_are_public=intent.selected_are_public,
                replacement_selections=intent.replacement_selections,
            )
        except LibrarySelectionError as exc:
            raise GameRuleError(str(exc)) from exc
        if (
            intent.arrangement.remainder_destination == "library_bottom"
            and not intent.looked_are_public
        ):
            for ref in intent.arrangement.remainder_refs:
                card = self._resolve_object(
                    intent.actor,
                    ref,
                    zones={"library"},
                    owned_only=True,
                )
                card.known_to = [intent.player]
                card.revealed_to = []
        if intent.looked_are_public or intent.selected_are_public:
            viewers = sorted(set(self.seats))
            for ref in intent.arrangement.selected_refs:
                card = self._resolve_object(intent.actor, ref)
                card.known_to = sorted(
                    set(card.known_to).union(viewers)
                )
                card.revealed_to = sorted(
                    set(card.revealed_to).union(viewers)
                )
        return looked

    def reorder_library_top_intent(
        self,
        intent: ReorderLibraryTopIntent,
    ) -> tuple[str, ...]:
        ids = [
            self._resolve_object(
                intent.viewer,
                ref,
                zones={"library"},
            ).object_id
            for ref in intent.refs_top_first
        ]
        library = self.state.players[intent.player].zones["library"]
        current_top = (
            list(reversed(library[-len(ids) :])) if ids else []
        )
        if (
            len(ids) != len(set(ids))
            or set(ids) != set(current_top)
            or any(
                intent.viewer not in self.state.cards[object_id].known_to
                for object_id in ids
            )
        ):
            raise GameRuleError(
                "Can only reorder the exact known cards currently on top"
            )
        for object_id in ids:
            library.remove(object_id)
        library.extend(reversed(ids))
        self._log(
            intent.actor,
            "library.reorder",
            f"{intent.viewer} reordered {len(ids)} known top cards.",
            {"count": len(ids)},
            visibility=[intent.viewer, "analyst"],
            importance=1,
            changed_objects=ids,
        )
        return intent.refs_top_first

    def pay_mana_cost_intent(self, intent: PayManaCostIntent) -> None:
        requirements = self._mana_vector(dict(intent.requirements))
        if not self._cost_is_affordable(intent.player, requirements):
            raise GameRuleError("The optional payment is no longer payable")
        self._pay_for_cost(
            intent.player,
            requirements,
            {"pay": "auto"},
        )
        changed_objects: list[str] = []
        if intent.changed_object_ref:
            card = self._resolve_object(intent.actor, intent.changed_object_ref)
            changed_objects.append(card.object_id)
        self._log(
            intent.actor,
            intent.event_code,
            intent.message,
            thaw_value(intent.details),
            importance=2,
            changed_objects=changed_objects,
            changed_players=[intent.player],
        )

    def place_counters_intent(
        self,
        intent: PlaceCountersIntent,
    ) -> tuple[str, ...]:
        try:
            results = place_counters_on_refs(
                self,
                actor=intent.actor,
                object_refs=intent.object_refs,
                counter_name=intent.counter_name,
                amount=intent.amount,
                selections=intent.replacement_selections,
                reason=intent.reason,
                source_ref=intent.source_ref,
            )
        except CounterPlacementError as exc:
            raise GameRuleError(str(exc)) from exc
        return tuple(
            self.state.cards[result.object_id].ref for result in results
        )

    def place_counter_batch_intent(
        self,
        intent: PlaceCounterBatchIntent,
    ) -> tuple[str, ...]:
        try:
            card = self._resolve_object(
                intent.actor,
                intent.object_ref,
                zones={"battlefield"},
            )
            results = place_counters(
                self,
                tuple(
                    CounterPlacementRequest(
                        subject_kind="permanent",
                        subject_id=card.object_id,
                        counter_name=placement.counter_name,
                        amount=placement.amount,
                        placing_player=intent.actor,
                        source_ref=intent.source_ref,
                    )
                    for placement in intent.placements
                ),
                selections=intent.replacement_selections,
                reason=intent.reason,
            )
        except CounterPlacementError as exc:
            raise GameRuleError(str(exc)) from exc
        return tuple(
            unique_preserving_order(
                self.state.cards[result.object_id].ref for result in results
            )
        )

    def remove_counters_intent(
        self,
        intent: RemoveCountersIntent,
    ) -> CounterRemovalResult:
        try:
            card = self._resolve_object(
                intent.actor,
                intent.object_ref,
                zones={"battlefield"},
            )
            plan = plan_counter_removal_effect(
                self,
                CounterRemoval(
                    object_id=card.object_id,
                    counter_name=intent.counter_name,
                    amount=intent.amount,
                    expected_zone="battlefield",
                    expected_logical_object_id=card.logical_object_id,
                ),
            )
            result = commit_counter_removal_effect(self, plan)
        except CounterRemovalError as exc:
            raise GameRuleError(str(exc)) from exc
        self._log(
            intent.actor,
            "permanent.counter",
            (
                f"{card.ref} {result.counter_name} changed by "
                f"{-result.removed}."
            ),
            {
                "object": card.ref,
                "counter": result.counter_name,
                "requested_delta": -result.requested,
                "delta": -result.removed,
                "before": result.before,
                "after": result.after,
                "source": intent.source_ref,
            },
            importance=1,
            changed_objects=([card.object_id] if result.removed else []),
        )
        if (
            result.counter_name == "defense"
            and result.before > 0
            and result.after == 0
        ):
            self._queue_siege_defeated_trigger(card)
        return result

    def remove_all_counters_intent(
        self,
        intent: RemoveAllCountersIntent,
    ) -> AllCounterRemovalResult:
        try:
            card = self._resolve_object(
                intent.actor,
                intent.object_ref,
                zones={"battlefield"},
            )
            result = commit_all_counter_removal_effect(
                self,
                plan_all_counter_removal_effect(
                    self,
                    object_id=card.object_id,
                    expected_zone="battlefield",
                    expected_logical_object_id=card.logical_object_id,
                ),
            )
        except CounterRemovalError as exc:
            raise GameRuleError(str(exc)) from exc
        removed = dict(result.removed)
        self._log(
            intent.actor,
            "permanent.counters_removed",
            f"{card.ref} had {result.total_removed} counter(s) removed.",
            {
                "object": card.ref,
                "removed": removed,
                "total_removed": result.total_removed,
                "source": intent.source_ref,
            },
            importance=1,
            changed_objects=([card.object_id] if result.total_removed else []),
        )
        if removed.get("defense", 0):
            self._queue_siege_defeated_trigger(card)
        return result

    def place_counters_on_set_intent(
        self,
        intent: PlaceCountersOnSetIntent,
    ) -> tuple[str, ...]:
        try:
            results = resolve_counter_placement_set(
                self,
                actor=intent.actor,
                spec=intent.spec,
                counter_name=intent.counter_name,
                amount=intent.amount,
                reason=intent.reason,
                source_ref=intent.source_ref,
                replacement_selections=tuple(
                    thaw_value(value)
                    for value in intent.replacement_selections
                ),
            )
        except CounterPlacementSetError as exc:
            raise GameRuleError(str(exc)) from exc
        return tuple(
            self.state.cards[result.object_id].ref for result in results
        )

    def place_counters_on_targets_intent(
        self,
        intent: PlaceCountersOnTargetsIntent,
    ) -> tuple[str, ...]:
        try:
            results = resolve_counter_placement_targets(
                self,
                actor=intent.actor,
                refs=intent.object_refs,
                maximum_targets=intent.maximum_targets,
                counter_name=intent.counter_name,
                amount=intent.amount,
                reason=intent.reason,
                source_ref=intent.source_ref,
                replacement_selections=tuple(
                    thaw_value(value)
                    for value in intent.replacement_selections
                ),
            )
        except CounterPlacementTargetSetError as exc:
            raise GameRuleError(str(exc)) from exc
        return tuple(
            self.state.cards[result.object_id].ref for result in results
        )

    def place_player_counters_intent(
        self,
        intent: PlacePlayerCountersIntent,
    ) -> tuple[str, ...]:
        for player in intent.player_ids:
            if player not in self.active_seats:
                raise GameRuleError(
                    "A player counter subject is no longer in the game"
                )
        try:
            results = place_counters(
                self,
                tuple(
                    CounterPlacementRequest(
                        subject_kind="player",
                        subject_id=player,
                        counter_name=intent.counter_name,
                        amount=intent.amount,
                        placing_player=intent.actor,
                        source_ref=intent.source_ref,
                    )
                    for player in intent.player_ids
                ),
                selections=intent.replacement_selections,
                reason=intent.reason,
            )
        except CounterPlacementError as exc:
            raise GameRuleError(str(exc)) from exc
        return tuple(result.subject_id for result in results)

    def counter_stack_intent(self, intent: CounterStackIntent) -> None:
        if any(item.ref == intent.stack_ref for item in self.state.stack):
            self._counter_stack_item(
                intent.stack_ref,
                reason=intent.reason,
                countered_by=intent.countered_by,
            )

    def eliminate_players_intent(self, intent: EliminatePlayersIntent) -> None:
        self._eliminate_players(list(intent.players), reason=intent.reason)

    def copy_stack_item_intent(self, intent: CopyStackItemIntent) -> str:
        target = next(
            (
                item
                for item in self.state.stack
                if item.ref == intent.target_stack_ref
            ),
            None,
        )
        if target is None:
            raise GameRuleError(
                "The stack object selected for copying no longer exists"
            )
        copied = self._copy_stack_item(
            controller=intent.controller,
            target=target,
            targets=list(intent.targets),
            target_groups=thaw_value(intent.target_groups),
            reason=intent.reason,
        )
        return copied.ref

    def retarget_stack_item_intent(
        self,
        intent: RetargetStackItemIntent,
    ) -> str:
        target = next(
            (
                item
                for item in self.state.stack
                if item.ref == intent.target_stack_ref
            ),
            None,
        )
        if target is None:
            raise GameRuleError(
                "The stack object selected for retargeting no longer exists"
            )
        target.targets = list(intent.targets)
        target.context["target_groups"] = thaw_value(intent.target_groups)
        target.context["target_snapshots"] = {
            ref: self._target_snapshot(ref) for ref in intent.targets
        }
        target.context["targets_revalidated"] = False
        self._log(
            intent.actor,
            "stack.retarget",
            f"{intent.actor} chose targets for {target.ref}.",
            {
                "stack": target.ref,
                "targets": list(intent.targets),
                "source": intent.source_stack_ref,
            },
            importance=2,
        )
        return target.ref

    def create_token_intent(
        self,
        intent: CreateTokenIntent,
    ) -> tuple[str, ...]:
        created_refs = tuple(
            self.create_token(
                intent.controller,
                name=intent.name,
                quantity=intent.quantity,
                copy_of=intent.copy_of,
                characteristics=thaw_value(intent.characteristics),
                temporary_keywords=intent.temporary_keywords,
                reason=intent.reason,
                replacement_selections=intent.replacement_selections,
            )
        )
        if not (
            intent.sacrifice_at_end_step
            or intent.sacrifice_on_controller_end_step
        ):
            return created_refs
        for created_ref in created_refs:
            created = self._resolve_object(
                intent.actor,
                created_ref,
                zones={"battlefield"},
                controlled_only=True,
            )
            condition: dict[str, Any] = {
                "phase": "ending",
                "step": "end_step",
            }
            if intent.sacrifice_on_controller_end_step:
                condition["player"] = "controller"
            schedule_delayed_trigger(
                self,
                controller=intent.controller,
                label=f"Sacrifice {created.ref}",
                event_kind="step.begin",
                condition=condition,
                stack_template={
                    "label": f"Sacrifice {created.ref}",
                    "semantic_key": "builtin:sacrifice-source",
                },
                source_object_id=created.object_id,
                once=True,
            )
        return created_refs

    def copy_controlled_tokens_intent(
        self,
        intent: CopyControlledTokensIntent,
    ) -> tuple[str, ...]:
        original = self._resolve_object(
            intent.actor,
            intent.chosen_token_ref,
            zones={"battlefield"},
            controlled_only=True,
        )
        if not original.is_token:
            raise GameRuleError("Token-copy choice requires a token")
        characteristics = self._copyable_characteristics(original)
        changed: list[str] = []
        for object_id in tuple(
            self.state.players[intent.controller].zones["battlefield"]
        ):
            token = self.state.cards[object_id]
            if (
                token.controller != intent.controller
                or not token.is_token
                or token.object_id == original.object_id
            ):
                continue
            token.annotations["copy_overrides"] = copy.deepcopy(
                characteristics
            )
            changed.append(token.object_id)
        changed_refs = tuple(
            self.state.cards[object_id].ref for object_id in changed
        )
        self._log(
            intent.actor,
            "token.copy_all",
            (
                f"{len(changed)} other token(s) became copies of "
                f"{original.ref}."
            ),
            {
                "source": intent.source_stack_ref,
                "chosen_token": original.ref,
                "objects": list(changed_refs),
            },
            importance=2,
            changed_objects=changed,
        )
        return changed_refs

    def add_subtype_intent(self, intent: AddSubtypeIntent) -> str:
        card = self._resolve_object(
            intent.actor,
            intent.object_ref,
            zones={"battlefield"},
        )
        try:
            effect = commit_zone_object_subtype_addition(
                self,
                card=card,
                source=ResolutionEffectSource(
                    stack_ref=intent.source.stack_ref,
                    object_id=intent.source.object_id,
                    logical_object_id=intent.source.logical_object_id,
                    card_ref=intent.source.card_ref,
                ),
                subtype=intent.subtype,
            )
        except ZoneObjectSubtypeGrantError as exc:
            raise GameRuleError(str(exc)) from exc
        self._log(
            intent.actor,
            "permanent.subtype",
            (
                f"{card.ref} became a {intent.subtype} in addition to its "
                "other types."
            ),
            {
                "object": card.ref,
                "subtype": intent.subtype,
                "continuous_effect": effect.effect_id,
                "reason": intent.reason,
            },
            importance=1,
            changed_objects=[card.object_id],
        )
        return card.ref

    def grant_zone_object_keyword_intent(
        self,
        intent: GrantZoneObjectKeywordIntent,
    ) -> str:
        card = self._resolve_object(
            intent.actor,
            intent.object_ref,
            zones={"battlefield"},
        )
        if card.phased_out:
            raise GameRuleError(
                "Zone-object keyword grants require a phased-in target"
            )
        try:
            commit_zone_object_keyword_grant(
                self,
                card=card,
                source=ResolutionEffectSource(
                    stack_ref=intent.source.stack_ref,
                    object_id=intent.source.object_id,
                    logical_object_id=intent.source.logical_object_id,
                    card_ref=intent.source.card_ref,
                ),
                keyword=intent.keyword,
            )
        except ZoneObjectKeywordGrantError as exc:
            raise GameRuleError(str(exc)) from exc
        self._log(
            intent.actor,
            "permanent.keyword",
            (
                f"{card.ref} gained {intent.keyword.title()} for this "
                "battlefield incarnation."
            ),
            {
                "object": card.ref,
                "keyword": intent.keyword,
                "duration": "zone_object",
                _REASON_FIELD: intent.reason,
            },
            importance=1,
            changed_objects=[card.object_id],
        )
        return card.ref

    def proliferate_intent(self, intent: ProliferateIntent) -> None:
        requests: list[CounterPlacementRequest] = []
        for subject in intent.subjects:
            if subject.subject_kind == "player":
                player = self.state.players.get(subject.subject_id)
                if player is None or not player.in_game:
                    raise GameRuleError(
                        "A Proliferate player is no longer in the game"
                    )
                current_names = tuple(player_counter_snapshot(player))
            else:
                card = self.state.cards.get(subject.subject_id)
                if (
                    card is None
                    or card.ref != subject.ref
                    or card.zone != "battlefield"
                    or card.phased_out
                    or card.logical_object_id
                    != subject.logical_object_id
                ):
                    raise GameRuleError(
                        "A Proliferate permanent changed identity before commit"
                    )
                current_names = tuple(
                    sorted(
                        name
                        for name, amount in card.counters.items()
                        if int(amount) > 0
                    )
                )
            if current_names != subject.counter_names:
                raise GameRuleError(
                    "A Proliferate subject's counter set changed before commit"
                )
            requests.extend(
                CounterPlacementRequest(
                    subject_kind=subject.subject_kind,
                    subject_id=subject.subject_id,
                    counter_name=name,
                    amount=1,
                    placing_player=intent.actor,
                    source_ref=intent.source_ref,
                )
                for name in subject.counter_names
            )
        try:
            results = place_counters(
                self,
                tuple(requests),
                selections=tuple(
                    thaw_value(value)
                    for value in intent.replacement_selections
                ),
                reason=intent.reason,
            )
        except CounterPlacementError as exc:
            raise GameRuleError(str(exc)) from exc
        changed_objects = sorted(
            {
                result.subject_id
                for result in results
                if result.subject_kind == "permanent"
                and result.after != result.before
            }
        )
        changed_players = sorted(
            {
                result.subject_id
                for result in results
                if result.subject_kind == "player"
                and result.after != result.before
            }
        )
        self._log(
            intent.actor,
            "counter.proliferate",
            f"{intent.actor} proliferated {len(intent.selections)} object(s).",
            {
                "subjects": list(intent.selections),
                "counter_placements": len(results),
                "source": intent.source_ref,
            },
            importance=2,
            changed_objects=changed_objects,
            changed_players=changed_players,
        )
