from __future__ import annotations
from ..rules.opponent_blight import OpponentBlightBatchIntent
from .token_creation_intents import execute_token_creation_intent
from ..semantic_runtime.stack_execution import execute_stack_copy
from ..semantic_runtime.life_batch_intent import LifeLossBatchIntent
from ..semantic_runtime.life_gain_cost_intent import LifeGainCostBatchIntent
from ..semantic_runtime.control_intents import (
    DirectionalControlIntent,
    GainControlIntent, GainControlSetIntent, GainSpellControlIntent,
    ExchangePermanentControlIntent, GiveControlIntent, GiveControlSetIntent, RestoreOwnerControlIntent,
)
from ..semantic_runtime.connive_intent import ConniveCompletedIntent
from ..semantic_runtime.exploit_intent import ExploitSacrificeIntent
from ..rules.committed_quantity_followups import CommittedQuantityFollowup

import copy
import random
from typing import Any

from ..counter_placement import (
    CounterPlacementError,
    CounterPlacementRequest,
    place_counters,
)
from ..counter_doubling import resolve_effect_counters_on_refs as place_counters_on_refs
from ..counter_placement_sets import CounterPlacementSetError
from ..counter_doubling import resolve_effect_counter_set as resolve_counter_placement_set
from ..counter_placement_targets import (
    CounterPlacementTargetSetError,
    resolve_counter_placement_targets,
)
from ..counter_removal import (
    AllPermanentCountersThenTokenExileIntent,
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
from ..fixed_damage_set import FixedDamageSetError, resolve_fixed_damage_set
from ..destruction_sets import DestructionSetError, resolve_destruction_set
from ..effect_runtime import dispatch_effect
from ..effect_runtime.hand_inspection import commit_hand_inspection
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
from ..carddb_characteristics import library_reveal_printed_identity
from ..semantic_runtime.intents import RevealEachLibraryTopIntent
from ..rules.library_scry import ScryError, commit_scry_arrangement
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
    WinGameIntent,
    ExploreCompletedIntent,
    GrantZoneObjectKeywordIntent,
    InspectHandIntent,
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
from .public_zone_move_intent import commit_public_zone_move_intent
from ..zone_object_subtype_grants import (
    ZoneObjectSubtypeGrantError,
    commit_zone_object_subtype_addition,
)
from ..semantic_runtime.zone_replacements import prepare_zone_change_replacement
from ..semantic_runtime.life_replacements import collect_life_change_replacement_effects


_REASON_FIELD = "reason"
from ..util import unique_preserving_order


class SemanticChoiceIntentHostMixin:
    def gain_control_intent(self, intent: GainControlIntent | GiveControlIntent | GainSpellControlIntent):
        from ..control_effects import ControlEffectError, execute_single_control_intent

        try:
            return execute_single_control_intent(self, intent)
        except ControlEffectError as exc:
            raise GameRuleError(str(exc)) from exc

    def gain_control_set_intent(self, intent: GainControlSetIntent | GiveControlSetIntent
                                | RestoreOwnerControlIntent | ExchangePermanentControlIntent | DirectionalControlIntent):
        from ..control_effects import (
            ControlEffectError, execute_control_set, execute_owner_control_restoration,
            execute_permanent_control_exchange, execute_recipient_control_set,
        )
        try:
            if isinstance(intent, DirectionalControlIntent):
                from ..rules.directional_control import execute_directional_control

                return execute_directional_control(self, intent)
            if isinstance(intent, ExchangePermanentControlIntent):
                return execute_permanent_control_exchange(self, intent)
            if isinstance(intent,RestoreOwnerControlIntent):return execute_owner_control_restoration(self,intent)
            if isinstance(intent,GiveControlSetIntent):return execute_recipient_control_set(self,intent)
            return execute_control_set(self,intent)
        except ControlEffectError as exc:
            raise GameRuleError(str(exc)) from exc

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

    def public_zone_move_rules_names(self, row) -> tuple[str, ...]:
        from ..carddb_characteristics import public_object_rules_names

        card = self.state.cards[row.object_id]
        return public_object_rules_names(card, self._effective_card_data(card), self.card_record(card))

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
            result = resolve_fixed_damage_set(
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
                source_override=(thaw_value(intent.source_snapshot) if intent.source_snapshot is not None else None),
                death_exile_scope=intent.death_exile_scope,
                allocated_recipients=intent.allocated_recipients,
            )
            if intent.gain_life_from_dealt:
                from ..rules.committed_quantity_followups import damage_dealt_life_followup

                return damage_dealt_life_followup(result.dealt_amount)
            return result
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
                ordered_exclusion=intent.ordered_exclusion,
                followup=intent.followup,
                excluded_created_tokens=intent.excluded_created_tokens,
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
        return commit_public_zone_move_intent(self, intent)

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
        note = annotation_key == "last_noted_mana_type"
        value = {"logical_object_id": card.logical_object_id, "mana_type": intent.value} if note else intent.value
        card.annotations[annotation_key] = value
        if intent.apply_as_subtype:
            card.annotations["chosen_creature_type_adds_subtype"] = True
        event_code, detail_key = {
            "chosen_name": ("card.name.chosen", "card_name"),
            "chosen_color": ("color.chosen", "color"),
            "chosen_creature_type": ("creature_type.chosen", "creature_type"),
            "chosen_basic_land_type": ("land_type.chosen", "basic_land_type"),
            "chosen_faction": ("faction.chosen", "faction"),
            "chosen_jeskai_temur_faction": ("faction.chosen", "faction"),
            "last_noted_mana_type": ("mana.type.noted", "mana_type"),
        }[annotation_key]
        self._log(
            intent.actor,
            event_code,
            f"{intent.actor} {'noted' if note else 'chose'} {intent.value} for {card.ref}.",
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
        from ..semantic_runtime.explore import commit_explore_completion
        commit_explore_completion(self, intent)

    def complete_connive_intent(self, intent: ConniveCompletedIntent) -> None:
        retained = intent.conniver
        current = self.state.cards.get(retained.object_id)
        details = {
            'player': retained.controller, 'controller': retained.controller,
            'conniver': retained.ref, 'conniver_object_id': retained.object_id,
            'conniver_logical_object_id': retained.logical_object_id,
            'count': intent.count, 'nonlands_discarded': intent.nonlands_discarded,
            'reason': intent.reason,
        }
        self._log(intent.actor, 'connive.complete', f'{retained.ref} connived for {retained.controller}.', details,
                  importance=2, changed_players=[retained.controller],
                  changed_objects=([retained.object_id] if current is not None
                                   and current.logical_object_id == retained.logical_object_id else []))
        self._dispatch_semantic_event('permanent.connives', details)

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
        if intent.entry_enchantment is not None:
            from ..zone_transitions import ZoneTransitionOwner

            if intent.new_controller != card.owner:
                raise GameRuleError("Enchantment return requires its original owner's control")
            ZoneTransitionOwner(self).move_card(
                card.object_id, "battlefield", controller=card.owner, tapped=tapped, reason=intent.reason,
                semantic_events=intent.semantic_events, putting_player=intent.actor,
                replacement_selections=replacement_selections, resolution_entry_enchantment=intent.entry_enchantment,
            )
            return card.ref if card.zone == "battlefield" else ""
        if intent.library_face_down_entry:
            from ..zone_transitions import ZoneTransitionOwner
            ZoneTransitionOwner(self).move_card(card.object_id, 'battlefield', controller=intent.actor,
                manifest_entry=intent.manifest_entry, cloak_entry=intent.cloak_entry,
                reason=intent.reason, semantic_events=True, putting_player=intent.actor,
                replacement_selections=replacement_selections)
            return card.ref if card.zone == 'battlefield' and card.face_down else ''
        from ..semantic_runtime.entry_enchantment_return_intent import EntryEnchantmentReturnIntent

        if isinstance(intent, EntryEnchantmentReturnIntent):
            from ..rules.entry_enchantment_setting import EntryEnchantmentSetting
            from ..zone_transitions import ZoneTransitionOwner

            if not card.is_card_object or card.is_token or card.owner != intent.new_controller:
                return ""
            moved = ZoneTransitionOwner(self).move_card(
                card.object_id, "battlefield", controller=card.owner, tapped=tapped, reason=intent.reason,
                semantic_events=True, putting_player=intent.actor, replacement_selections=replacement_selections,
                resolution_entry_enchantment=EntryEnchantmentSetting(intent.entry_source_ref),
            )
            return moved.ref if moved.zone == "battlefield" else ""
        if intent.transformed_entry:
            from ..linked_exile_return import return_tapped_transformed_graveyard_card

            return return_tapped_transformed_graveyard_card(self, card, intent, replacement_selections)
        if intent.effect_entry_counters:
            prepared_replacement = prepare_zone_change_replacement(
                self,
                card,
                intent.destination,
                destination_controller=intent.new_controller,
                requested_tapped=tapped is True,
                effect_entry_counters=intent.effect_entry_counters,
                transition_kind=intent.transition_kind,
                selections=replacement_selections,
                error_type=GameRuleError,
            )
            replacement_selections = ()
        self.move_card(
            card.object_id,
            intent.destination,
            controller=intent.new_controller,
            **({"putting_player": intent.actor}
               if intent.destination == "battlefield" and card.zone != "battlefield" else {}),
            tapped=tapped,
            reason=intent.reason,
            semantic_events=intent.semantic_events,
            replacement_selections=replacement_selections,
            prepared_replacement=prepared_replacement,
            transition_kind=intent.transition_kind,
        )
        return card.ref

    def exploit_sacrifice_intent(self,intent: ExploitSacrificeIntent):
        from ..zone_trigger_processing import capture_departure_trigger_sources
        from ..zone_trigger_events import ZoneTransitionKind
        card=self._resolve_object(intent.actor,intent.object_refs[0],zones={'battlefield'},controlled_only=True)
        if card.logical_object_id!=intent.sacrificed_identity or card.phased_out:
            raise GameRuleError('Exploit selected sacrifice incarnation changed')
        if 'creature' not in self._type_parts(str(self._effective_card_data(card)['type_line']))[0]:
            raise GameRuleError('Exploit can sacrifice only a creature')
        snapshot=capture_departure_trigger_sources(self,semantic_events=True,origin='battlefield')
        source=next((candidate for candidate in snapshot.sources if candidate.ref==intent.source_ref
                     and candidate.logical_object_id==intent.source_identity),None)
        self._move_cards_simultaneously([(card.object_id,'graveyard')],reason=intent.reason,log=False,
            replacement_selections=intent.replacement_selections,transition_kinds={card.object_id:ZoneTransitionKind.SACRIFICE})
        committed=any(event.object_incarnation==intent.sacrificed_identity for event in self._current_turn_history('permanent_sacrificed'))
        if committed and source is not None:
            from ..zone_trigger_events import sealed_public_characteristic_facts
            details={'card':source.ref,'controller':source.controller,'player':intent.actor,
                'exploiter_logical_object_id':intent.source_identity,'exploited_card':card.ref,
                'exploited_logical_object_id':intent.sacrificed_identity,
                'exploited_characteristics':sealed_public_characteristic_facts(snapshot.source_characteristics.get(card.object_id,{}))}
            self._log(intent.actor,'exploit.complete',f'{source.ref} exploited {card.ref}.',details,importance=2)
            self._dispatch_semantic_event('creature.exploits',details,sources=snapshot.sources,
                source_zones=snapshot.source_zones,source_characteristics=snapshot.source_characteristics)
        return card.ref

    def move_objects_simultaneously_intent(
        self,
        intent: MoveObjectsSimultaneouslyIntent,
        *,
        graveyard_orders=None,
        require_graveyard_order=False,
        prepared_composition=None,
    ) -> tuple[str, ...] | CommittedQuantityFollowup | int:
        from .simultaneous_movement import commit_simultaneous_movement
        return commit_simultaneous_movement(
            self, intent, graveyard_orders=graveyard_orders, require_graveyard_order=require_graveyard_order,
            prepared_composition=prepared_composition,
        )

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

    def apply_life_gain_cost_batch_intent(self, intent: LifeGainCostBatchIntent) -> int:
        from .life_intent_commit import apply_life_gain_cost_batch_intent
        return apply_life_gain_cost_batch_intent(self,intent)

    def apply_life_loss_batch_intent(self, intent: LifeLossBatchIntent) -> int:
        from .life_intent_commit import apply_life_loss_batch_intent
        return apply_life_loss_batch_intent(self,intent)

    def apply_life_change_intent(self, intent: LifeChangeIntent) -> int:
        from .life_intent_commit import apply_life_change_intent
        return apply_life_change_intent(self,intent)

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
        *,
        public_top_name: str | None = None,
    ) -> tuple[str, ...]:
        if type(intent.announce_public_top) is not bool or intent.announce_public_top and (
            not intent.public or intent.player != intent.actor or intent.viewer != intent.actor
            or len(intent.refs_top_first) != 1 or public_top_name is not None
        ):
            raise GameRuleError("A controller's public top reveal has one exact actor and card")
        if type(intent.retain_public_top_visibility) is not bool or (
            intent.retain_public_top_visibility and not (intent.announce_public_top or intent.announce_public_prefix)
        ):
            raise GameRuleError("Only an announced public library reveal can stay revealed")
        if public_top_name is not None and (not intent.public or len(intent.refs_top_first) != 1):
            raise GameRuleError("A public top-card announcement requires one public reveal")
        if type(intent.announce_public_prefix) is not bool or intent.announce_public_prefix and (
            not intent.public or intent.player != intent.actor or intent.viewer != intent.actor
            or not intent.refs_top_first or intent.announce_public_top or public_top_name is not None
        ):
            raise GameRuleError("A public prefix announcement requires the library player's exact public reveal")
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
        if intent.announce_public_prefix:
            from .library_selection_intent_commit import announce_public_library_prefix

            if not intent.retain_public_top_visibility:
                return announce_public_library_prefix(self, intent, tuple(reversed(library[-len(refs):])))
        if intent.announce_public_top:
            card = self.state.cards[library[-1]]
            if card.object_kind != "card":
                public_top_name = card.printed_name
            else:
                public_top_name, _ = library_reveal_printed_identity(self.card_db.by_oracle_id(card.oracle_id))
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
            if intent.public and (public_top_name is None or intent.retain_public_top_visibility):
                card.revealed_to = sorted(
                    set(card.revealed_to).union(viewers)
                )
            object_ids.append(card.object_id)
        if intent.announce_public_prefix:
            return announce_public_library_prefix(self, intent, tuple(object_ids))
        if public_top_name is not None:
            self._log(
                intent.actor, "library.top.reveal", f"{intent.player} revealed {public_top_name}.",
                {"player": intent.player, "card": public_top_name}, changed_objects=object_ids,
            )
            return refs
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

    def reveal_each_library_top_intent(
        self, intent: RevealEachLibraryTopIntent,
    ) -> CommittedQuantityFollowup:
        self._require_seat(intent.actor, in_game=True)
        reveals = []
        land_count = 0
        for seat in self.apnap_order():
            library = self.state.players[seat].zones["library"]
            if not library:
                continue
            card = self.state.cards[library[-1]]
            record = self.card_db.by_oracle_id(card.oracle_id)
            name, type_line = library_reveal_printed_identity(record)
            land_count += "land" in self._type_parts(type_line)[0]
            reveals.append((seat, card.ref, name))

        for seat, ref, name in reveals:
            self.reveal_library_cards_intent(
                RevealLibraryCardsIntent(intent.actor, seat, seat, (ref,), intent.reason, public=True),
                public_top_name=name,
            )
        return intent.followup.from_revealed_counts(lands=land_count, nonlands=len(reveals) - land_count)

    def inspect_hand_intent(
        self,
        intent: InspectHandIntent,
    ) -> tuple[str, ...]:
        return commit_hand_inspection(self, intent)

    def reveal_colored_hand_cards_intent(self, intent):
        from ..effect_runtime.hand_inspection import commit_colored_hand_reveal
        return commit_colored_hand_reveal(self,intent)

    def move_library_cards_to_bottom_intent(
        self,
        intent: MoveLibraryCardsToBottomIntent,
    ) -> tuple[str, ...]:
        if intent.random_source_stack_ref is not None:
            from .library_selection_intent_commit import commit_private_random_library_bottom

            return commit_private_random_library_bottom(self, intent)
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
                requested_count=intent.requested_count,
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
        self, intent: LibrarySelectionIntent,
    ) -> tuple[str, ...] | CommittedQuantityFollowup:
        from .library_selection_intent_commit import commit_library_selection_intent

        return commit_library_selection_intent(self, intent)

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
        if type(intent.shuffle_remainder) is not bool:
            raise GameRuleError("Private top ordering needs a typed shuffle instruction")
        if intent.shuffle_remainder:
            if intent.actor != intent.player or intent.viewer != intent.player or not 1 <= len(ids) <= 5:
                raise GameRuleError("Shuffle and restore requires the controller's exact looked group")
            from ..zone_transitions import ZoneTransitionOwner

            ZoneTransitionOwner(self).shuffle_library(
                intent.player, reason=intent.reason, excluded_object_ids=tuple(ids),
            )
            if len(ids) > 1:
                for object_id in ids:
                    card = self.state.cards[object_id]
                    card.known_to = [intent.viewer]
                    card.revealed_to = []
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
        from ..rules.mana_pool_payment import MANA_POOL_PAYMENT_FIELD

        requirements = self._mana_vector(dict(intent.requirements))
        if not self._cost_is_affordable(intent.player, requirements):
            raise GameRuleError("The optional payment is no longer payable")
        self._pay_for_cost(
            intent.player,
            requirements,
            ({"pay": "auto"} if intent.pool_payment is None
             else {MANA_POOL_PAYMENT_FIELD: thaw_value(intent.pool_payment)}),
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
                effect_generated=intent.effect_generated,
            )
        except CounterPlacementError as exc:
            raise GameRuleError(str(exc)) from exc
        return tuple(
            self.state.cards[result.object_id].ref for result in results
        )

    def place_opponent_blight_batch_intent(self, intent: OpponentBlightBatchIntent) -> tuple[str, ...]:
        from .blight import commit_opponent_blight_batch

        return commit_opponent_blight_batch(self, intent)

    def place_counter_batch_intent(
        self,
        intent: PlaceCounterBatchIntent,
    ) -> tuple[str, ...]:
        try:
            cards = tuple(self._resolve_object(intent.actor, ref, zones={"battlefield"})
                          for ref in intent.object_refs)
            if intent.recipient_identity is not None and (
                    cards[0].logical_object_id != intent.recipient_identity
                    or not intent.source_departure and cards[0].controller != intent.actor
                    or cards[0].phased_out):
                return ()
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
                    for card in cards
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
        intent: RemoveAllCountersIntent | AllPermanentCountersThenTokenExileIntent,
    ) -> AllCounterRemovalResult | tuple[AllCounterRemovalResult, ...]:
        if isinstance(intent, AllPermanentCountersThenTokenExileIntent):
            from ..counter_removal import commit_all_permanent_counter_removals

            try:
                results = commit_all_permanent_counter_removals(self)
            except CounterRemovalError as exc:
                raise GameRuleError(str(exc)) from exc
            for result in results:
                self._record_all_removed_counters(
                    result, self.state.cards[result.object_id], actor=intent.actor, source_ref=intent.source_ref,
                )
            token_ids = sorted(card.object_id for card in self.state.cards.values()
                               if card.zone == "battlefield" and card.is_token and not card.phased_out)
            if token_ids:
                self._move_cards_simultaneously(
                    [(object_id, "exile") for object_id in token_ids], reason=intent.reason, log=True,
                )
            return results
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
                    counter_name=intent.counter_name,
                ),
            )
        except CounterRemovalError as exc:
            raise GameRuleError(str(exc)) from exc
        self._record_all_removed_counters(result, card, actor=intent.actor, source_ref=intent.source_ref)
        return result

    def _record_all_removed_counters(self, result, card, *, actor, source_ref):
        removed = dict(result.removed)
        self._log(
            actor,
            "permanent.counters_removed",
            f"{card.ref} had {result.total_removed} counter(s) removed.",
            {
                "object": card.ref,
                "removed": removed,
                "total_removed": result.total_removed,
                "source": source_ref,
            },
            importance=1,
            changed_objects=([card.object_id] if result.total_removed else []),
        )
        if removed.get("defense", 0):
            self._queue_siege_defeated_trigger(card)

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
                paired_loyalty_source=intent.paired_loyalty_source,
                source_and_subtype_identity=intent.source_and_subtype_identity,
                declared_attack_members=intent.declared_attack_members,
                replacement_selections=tuple(
                    thaw_value(value)
                    for value in intent.replacement_selections
                ),
            )
        except CounterPlacementSetError as exc:
            raise GameRuleError(str(exc)) from exc
        return tuple(unique_preserving_order(self.state.cards[result.object_id].ref for result in results))

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
                target_shares=intent.target_shares,
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
        from ..rad_counter_model import RAD_COUNTER
        from ..rad_counters import require_rad_authority

        if intent.counter_name == RAD_COUNTER:
            require_rad_authority(self)
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
        if not any(item.ref == intent.stack_ref for item in self.state.stack):
            return
        if intent.activation_prohibition is not None:
            from ..stack_counter import counter_ability_with_activation_lock

            prohibition = intent.activation_prohibition
            counter_ability_with_activation_lock(
                self, intent.stack_ref, source=prohibition.source,
                actor=intent.actor, reason=intent.reason, mode=prohibition.mode,
            )
            return
        if intent.ability_source_loss is not None:
            from ..stack_counter import counter_ability_with_source_loss

            counter_ability_with_source_loss(
                self, intent.stack_ref, source=intent.ability_source_loss,
                actor=intent.actor, reason=intent.reason,
            )
            return
        if intent.counter_destination is not None:
            from ..stack_counter import counter_stack_item

            counter_stack_item(
                self, intent.stack_ref, reason=intent.reason, countered_by=intent.countered_by,
                source_controller=intent.source_controller,
                counter_destination=intent.counter_destination, replacement_selections=intent.replacement_selections,
            )
            return
        self._counter_stack_item(
            intent.stack_ref, reason=intent.reason, countered_by=intent.countered_by,
            source_controller=intent.source_controller,
        )

    def player_outcome_intent(self, intent: EliminatePlayersIntent | WinGameIntent) -> None:
        from ..rules.match_outcome_restrictions import current_match_outcome_restrictions
        from ..state_based_execution import commit_game_win

        restrictions = current_match_outcome_restrictions(self)
        if isinstance(intent, WinGameIntent):
            if intent.player in self.active_seats and intent.player not in restrictions.win_prohibited:
                commit_game_win(self, intent.player)
            return
        allowed = [seat for seat in intent.players if seat not in restrictions.loss_prohibited]
        self._eliminate_players(allowed, reason=intent.reason)

    def copy_stack_item_intent(self, intent: CopyStackItemIntent) -> str | None:
        return execute_stack_copy(self, intent)

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
        previous_targets = tuple(target.targets)
        previous_target_snapshots = target.context.get("target_snapshots", {})
        target.targets = list(intent.targets)
        target.context["target_groups"] = thaw_value(intent.target_groups)
        target.context["target_snapshots"] = (
            thaw_value(intent.target_snapshots) if intent.target_snapshots is not None
            else {ref: self._target_snapshot(ref) for ref in intent.targets}
        )
        target.context["targets_revalidated"] = False
        from ..rules.target_announcements import dispatch_target_announcements
        dispatch_target_announcements(
            self, target, previous_targets=previous_targets, previous_target_snapshots=previous_target_snapshots,
            collect_ward=intent.collect_single_target_ward or intent.target_snapshots is not None,
        )
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
        return execute_token_creation_intent(self, intent)

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
        from ..entry_results import record_persistent_copy

        current_copy = intent.schema_version == 2
        if not original.is_token:
            raise GameRuleError("Token-copy choice requires a token")
        if current_copy and (
            original.phased_out or original.controller != intent.controller or intent.controller != intent.actor
        ):
            raise GameRuleError("Token-copy choice requires a present controlled token")
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
                or (current_copy and token.phased_out)
            ):
                continue
            if current_copy:
                record_persistent_copy(token, characteristics, host=self)
            else:
                # Archived mandatory-copy payloads retain their original state transition.
                token.annotations["copy_overrides"] = copy.deepcopy(characteristics)
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
    ) -> str | tuple[str, ...]:
        source = ResolutionEffectSource(
            stack_ref=intent.source.stack_ref, object_id=intent.source.object_id,
            logical_object_id=intent.source.logical_object_id, card_ref=intent.source.card_ref,
        )
        if intent.controlled_creature_set:
            from ..zone_object_keyword_grants import commit_controlled_creature_indestructible

            try:
                return commit_controlled_creature_indestructible(self, actor=intent.actor,
                    source=source, reason=intent.reason)
            except ZoneObjectKeywordGrantError as exc:
                raise GameRuleError(str(exc)) from exc
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
                source=source,
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

    def proliferate_intent(self, intent: ProliferateIntent) -> CommittedQuantityFollowup | None:
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
        if intent.choose_phase_out:
            from ..rules.proliferate_phase_out import committed_proliferate_phase_choice

            return committed_proliferate_phase_choice(self, intent.actor, results)
        return None
