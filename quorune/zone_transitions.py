from __future__ import annotations

from .private_opponent_library_cast_model import PrivateLibrarySearchPlaySource

"""Authoritative zone membership and logical-incarnation transitions.

The rules engine remains the transaction facade and supplies typed replacement,
entry, attachment, characteristic, and trigger collaborators.  This module owns
the actual zone-list mutation, CR 400.7 incarnation reset, timestamp allocation,
last-known-information capture, and normalized zone-change announcement.
"""

import copy
import random
from typing import Any, Iterable, Mapping, Sequence

from . import control_history
from .continuous_effect_state import expire_zone_departure_effects
from .token_creation import token_copy_snapshot
from .attachments import (
    attach_objects,
    attachment_target_ref,
    clear_object_attachment_relations,
    take_pending_attachment,
)
from .aura import commit_aura_zone_move, preflight_aura_zone_move
from .entry_counters import (
    EffectEntryCounter,
    capture_prospective_entry_characteristics,
    mark_intrinsic_entry_counters_initialized,
    prospective_battle_entry_protector,
)
from . import entry_results
from .errors import GameRuleError, StateInvariantError
from .library_order_model import LibraryArrivalOrder
from .rules.graveyard_arrival_order import place_ordered_graveyard_arrivals, validate_prepared_graveyard_orders
from .rules.entry_subtype_grants import EntrySubtypeGrant
from .rules.entry_enchantment_setting import (
    EntryEnchantmentSetting, plan_resolution_entry_characteristics,
)
from .rules.library_partition import (
    commit_ordered_library_arrivals, publish_library_search_reveal, validate_ordered_library_destination,
    validated_library_search_results,
)
from .life_state import (
    LifeStateError,
    commit_life_payment,
)
from .model import CardInstance, GameState, StackItem
from .relative_power_target import pin_host_relative_power_source_departures
from .zone_trigger_processing import _damage_source_characteristics
from .rules.permanent_event_state import current_zone_event_characteristics, with_zone_event_numerics
from .scalar_effect_amounts import pin_scalar_characteristic_departures
from .station import pin_host_station_departures
from .semantic_runtime import (
    PreparedZoneChange,
    log_applied_zone_replacements,
    prepare_zone_change_replacement,
    prepare_zone_change_replacement_batch,
)
from .counter_placement_events import dispatch_counter_event_trees
from .semantic_runtime.explore import capture_explore_source_departure
from .counter_placement_events import dispatch_counter_event_trees
from .resolving_library_exile import resolved_library_exile_trigger_batch
from .trigger_processing import enqueue_trigger_batch, pin_zone_departure_trigger_facts
from .zone_object_state import (
    apply_library_exile_visibility, mark_card_manifested, reset_card_after_zone_change,
    stack_entry_cast_facts, validate_face_down_library_entry, validate_library_face_down_exile_move,
)
from .multikicker_result_amounts import needs_multikicker_entry_fact
from .discard_effect_origin import DiscardEffectOrigin
from .zone_trigger_events import (
    ZoneChangeOccurrence,
    ZoneTransitionKind,
    normalized_library_position,
    normalized_transition_kind_map,
    validate_zone_transition_request,
)
from .zone_trigger_processing import (
    dispatch_committed_batch_zone_events,
    dispatch_committed_discard_groups,
    capture_departure_trigger_sources,
    capture_zone_trigger_snapshot,
    dispatch_zone_change_occurrence,
    record_zone_change_history,
    semantic_event_sources,
)
from .day_night import synchronize_day_night
from .permanent_transform import trusted_daybound_entry_face
from .zone_transition_model import (
    library_insertion_index, replacement_library_position,
    batch_putting_players, capture_simultaneous_departure, entry_putting_player,
    filter_prohibited_sacrifice_transitions, sacrifice_transition_is_prohibited, validate_simultaneous_controllers,
    prepare_entry_replacement_for_move, prepare_move_entry_characteristics, prepare_move_entry_life_payment,
    EXILE_ZONE,
    JOURNAL_REASON_FIELD,
    LIBRARY_ZONE,
    PUBLIC_ZONES,
    ZoneDepartureSnapshot,
    ZoneMovePlan,
    ZoneTransitionHost,
)
from .zone_transition_journal import (
    finish_self_graveyard_shuffles,
    journal_prevented_nonpermanent_move,
    journal_prevented_token_move,
    journal_zone_move,
    prepare_zone_side_effects, validate_hidden_search_result_journal,
)


class ZoneTransitionOwner:
    """Single authoritative mutation owner for represented zone changes."""

    def __init__(self, host: ZoneTransitionHost) -> None:
        self.host = host

    @property
    def state(self) -> GameState:
        """Expose the canonical write path to structural ownership audits."""

        return self.host.state

    def next_timestamp(self) -> int:
        self.state.timestamp_sequence += 1
        return self.state.timestamp_sequence

    def turn_face_up(self,card:CardInstance,*,viewers:tuple[str,...],copy_selections=())->bool:
        if not isinstance(card,CardInstance) or self.state.cards.get(card.object_id) is not card:
            raise GameRuleError('Turning face up requires its exact current object')
        if card.permanent_composition is not None:
            from .continuous_effect_model import ContinuousObjectIdentity
            from .rules.composition_faces import prepare_composition_face_up
            prepared=prepare_composition_face_up(self.host,
                ContinuousObjectIdentity(card.object_id,card.logical_object_id))
            if prepared is None or prepared.viewers!=viewers:
                raise GameRuleError('Composed face-up viewers or state changed')
            self.commit_composition_face_up(prepared,copy_selections=copy_selections)
            return prepared.can_turn_face_up
        if card.zone=='battlefield' and card.face_down and not card.phased_out and not card.is_token and card.object_kind=='card':
            from .rules.composition_faces import _physical_turn_up_face
            _face,type_line=_physical_turn_up_face(self.host,card,is_token=False)
            if {'instant','sorcery'}.intersection(self.host._type_parts(type_line)[0]):
                self.reveal_face_up_refusal(card,viewers=viewers,components=(
                    {'object':card.ref,'oracle_id':card.oracle_id,'card':card.printed_name},))
                return False
        self._turn_face_up_status(card,viewers=viewers,copy_selections=copy_selections)
        return True

    def turn_face_down(self,card:CardInstance)->bool:
        from .composition_face_changes import commit_face_down_status
        return commit_face_down_status(self,card)

    def reveal_face_up_refusal(self,card,*,viewers,components):
        if (self.state.cards.get(card.object_id) is not card or card.zone!='battlefield' or not card.face_down
                or not isinstance(viewers,tuple) or viewers!=tuple(self.host.seats)):
            raise GameRuleError('Refused face-up reveal requires its exact facedown object and public viewers')
        card.known_to=sorted(set(card.known_to).union(viewers))
        self.host._log(card.controller,'card.reveal','A face-down permanent was revealed and remained face down.',
            {'player':card.controller,'object':card.ref,'components':list(components),'reason':'face_up_refused'},
            changed_objects=[card.object_id],changed_players=[card.controller],importance=1)

    def _turn_face_up_status(self,card:CardInstance,*,viewers:tuple[str,...],copy_selections=(),prospective_composition=None)->None:
        from .composition_face_changes import commit_face_up_status
        commit_face_up_status(self,card,viewers=viewers,copy_selections=copy_selections,prospective_composition=prospective_composition)

    def pin_characteristic_departures(
        self,
        cards: Sequence[CardInstance],
    ) -> None:
        """Capture pending characteristic LKI as one rollback-safe update."""

        contexts = tuple(
            (item, copy.deepcopy(item.context))
            for item in self.state.stack
        )
        pending_before = tuple(self.state.pending_trigger_batches)
        try:
            pin_zone_departure_trigger_facts(self.host, cards, error_type=StateInvariantError)
            from .rules.source_charge_life_totals import pin_source_charge_life_departures
            pin_source_charge_life_departures(self.host, cards, error_type=StateInvariantError)
            from .target_controller_information import pin_target_controller_departures
            pin_target_controller_departures(self.host,cards,error_type=StateInvariantError)
            from .attachment_information import pin_attachment_information_departures
            pin_attachment_information_departures(self.host,cards,error_type=StateInvariantError)
            pin_scalar_characteristic_departures(self.host, cards, error_type=StateInvariantError)
            from .token_copy_references import pin_copy_characteristic_departures
            pin_copy_characteristic_departures(self.host, cards, error_type=StateInvariantError)
            pin_host_relative_power_source_departures(
                self.host,
                cards,
                error_type=StateInvariantError,
            )
            pin_host_station_departures(
                self.host,
                cards,
                error_type=StateInvariantError,
            )
            from .rules.stack_damage_sources import pin_stack_damage_departures, pin_creature_power_damage_departures
            pin_stack_damage_departures(self.host, cards, error_type=StateInvariantError)
            from .target_source import pin_targeting_source_departures
            pin_targeting_source_departures(self.host,cards,error_type=StateInvariantError)
            pin_creature_power_damage_departures(self.host, cards, error_type=StateInvariantError)
        except StateInvariantError:
            for item, context in contexts:
                item.context = context
            from .trigger_processing import replace_pending_trigger_batch
            for index, batch in enumerate(pending_before):
                replace_pending_trigger_batch(self.host, index, batch)
            raise

    def semantic_event_sources(self, *, zones: set[str] | None = None) -> list[CardInstance]:
        return semantic_event_sources(
            self.state.cards.values(),
            active_seats=self.host.active_seats,
            zones=zones,
        )

    def remember_conditional_exile(self, binding: Any) -> None:
        """Persist a created CR610.3 instruction on its original source."""
        from .conditional_exile_model import (
            CONDITIONAL_EXILE_BINDINGS, ConditionalExileBinding,
            conditional_source_remains, source_conditional_bindings,
        )
        if not isinstance(binding, ConditionalExileBinding) or not conditional_source_remains(binding.source, self.state.cards):
            raise GameRuleError('Conditional exile storage requires its current battlefield source')
        source = self.state.cards[binding.source.object_id]
        try:
            previous = source_conditional_bindings(source)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        if any(value.binding_id == binding.binding_id for value in previous):
            raise GameRuleError('Conditional exile instruction has already been stored')
        if any(not identity.matches_exiled_card(self.state.cards.get(identity.object_id)) for identity in binding.objects):
            raise GameRuleError('Conditional exile storage requires actual exiled incarnations')
        source.annotations[CONDITIONAL_EXILE_BINDINGS] = [value.to_dict() for value in (*previous, binding)]

    def remove_from_zone(self, card: CardInstance) -> None:
        if card.zone == "stack":
            return
        for player in self.state.players.values():
            ids = player.zones.get(card.zone)
            if ids is not None and card.object_id in ids:
                ids.remove(card.object_id)
                return
        if card.zone != "outside":
            raise StateInvariantError(f"Could not remove {card.ref} from {card.zone}")

    def _depart_stack_object(self,card:CardInstance,*,preserve_resolving:bool=True)->None:
        from .rules.event_spell_copy import pin_event_spell_copy_departure
        from .effect_runtime.hand_inspection import cleanup_stack_cost_hand_reveals
        for item in tuple(self.state.stack):
            if item.card_object_id==card.object_id:
                pin_event_spell_copy_departure(self.host,item)
        for item in tuple(self.state.stack):
            if item.card_object_id==card.object_id:
                cleanup_stack_cost_hand_reveals(self.host,item)
        if not preserve_resolving or not any(item.card_object_id==card.object_id
                and item.context.get('currently_resolving') for item in self.state.stack):
            self.state.stack[:]=[item for item in self.state.stack if item.card_object_id!=card.object_id]

    def commit_composition_merge(self,prepared):
        """Commit physical storage only; the resolving rule owns permission and semantics."""
        from .rules.composition_merge import PreparedCompositionMerge,prepare_composition_merge
        if not isinstance(prepared,PreparedCompositionMerge):raise GameRuleError('Physical merge preparation is missing')
        with self.host.transaction():
            current=prepare_composition_merge(self.host,target=prepared.root_identity,
                source=prepared.incoming.identity,stack_ref=prepared.incoming.stack_ref,position=prepared.position)
            if current!=prepared:raise GameRuleError('Physical merge preparation is stale')
            root=self.state.cards[prepared.root_identity.object_id]
            incoming=self.state.cards[prepared.incoming.identity.object_id]
            storage=prepared.storage
            if self.next_timestamp()!=prepared.timestamp:raise StateInvariantError('Merge timestamp changed during commitment')
            self._depart_stack_object(incoming,preserve_resolving=False)
            expire_zone_departure_effects(self.state, incoming)
            from .game_object_store import GameObjectStore
            GameObjectStore(self.host).commit(removals=(incoming.object_id,))
            root.permanent_composition=storage
            root.is_token=storage.topology.is_token
            root.object_kind='token' if root.is_token else 'card'
            root.is_commander=storage.topology.is_commander
            root.face_down=storage.parts[0].card_state['face_down']
            root.active_face=None
            storage.validate_carrier(root)
            self.host._log(prepared.chooser,'composition.merge',f'{root.ref} incorporated another physical part.',
                {'object':root.ref,'part':incoming.ref,'position':prepared.position.value},
                changed_objects=[root.object_id],importance=1)
            return storage

    def commit_composition_transform(self,prepared):
        from .composition_face_changes import commit_composition_transform
        return commit_composition_transform(self,prepared)

    def commit_composition_face_up(self,prepared,*,copy_selections=()):
        from .composition_face_changes import commit_composition_face_up
        return commit_composition_face_up(self,prepared,copy_selections=copy_selections)

    def depart_composition(self,object_id,destination,**kwargs):
        from .composition_departure_commit import depart_composition
        return depart_composition(self,object_id,destination,**kwargs)

    def reset_zone_change(
        self,
        card: CardInstance,
        destination: str,
        *,
        zone_timestamp: int | None = None,
        revealed_library_reorder: bool = False,
    ) -> None:
        if card.permanent_composition is not None:
            raise GameRuleError('Composed-permanent reset requires its complete physical group transaction')
        origin = card.zone
        if revealed_library_reorder and (origin != LIBRARY_ZONE or destination != LIBRARY_ZONE):
            raise GameRuleError("A revealed library reorder requires a current library object")
        creates_new_object = (
            origin != destination or origin in {EXILE_ZONE, "command"} or revealed_library_reorder
        )
        if not creates_new_object:
            return
        stack_to_battlefield = origin == "stack" and destination == "battlefield"
        if origin in {"battlefield", "stack"} and not stack_to_battlefield:
            expire_zone_departure_effects(self.state, card)
        self.host._remove_object_from_combat(
            card,
            reason=f"zone change to {destination}",
        )
        if not stack_to_battlefield:
            card.zone_change_counter += 1
        card.zone_timestamp = (
            int(zone_timestamp)
            if zone_timestamp is not None
            else self.next_timestamp()
        )
        card.world_supertype_timestamp = None
        if card.is_token and origin == "battlefield" and destination != "battlefield":
            card.has_left_battlefield = True
        clear_object_attachment_relations(
            self.state.cards,
            card,
            players=self.state.players,
        )
        reset_card_after_zone_change(
            card,
            destination=destination,
            stack_to_battlefield=stack_to_battlefield,
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
        aura_enchant_spec: Any = None,
        zone_timestamp: int | None = None,
        position: str | int = "top",
        reveal_to: Iterable[str] | None = None,
        reason: str = "",
        log: bool = True,
        semantic_events: bool = False,
        replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
        prepared_replacement: PreparedZoneChange | None = None,
        transition_kind: ZoneTransitionKind = ZoneTransitionKind.ORDINARY,
        characteristic_lki_prepared: bool = False,
        defer_control_sync: bool = False,
        manifest_entry: bool = False,
        cloak_entry: bool = False,
        _devour_batch: bool = False,
        discard_effect_origin: DiscardEffectOrigin | None = None,
        putting_player: str | None = None,
        private_library_exile_viewer: str | None = None,
        unviewed_library_exile: bool = False,
        private_replacement_journal: bool = False,
        hidden_search_result_viewer: str | None = None,
        private_library_cast_source: str | PrivateLibrarySearchPlaySource | None = None,
        resolution_entry_subtype: EntrySubtypeGrant | None = None,
        resolution_entry_enchantment: EntryEnchantmentSetting | None = None,
    ) -> CardInstance:
        if type(defer_control_sync) is not bool:
            raise GameRuleError("Zone-move control synchronization policy must be boolean")
        card = validate_zone_transition_request(
            self.state.cards,
            object_id,
            destination,
            transition_kind,
        )
        if sacrifice_transition_is_prohibited(self.state, card, transition_kind):
            return card
        validate_library_face_down_exile_move(
            card, destination=destination, reveal_to=reveal_to, unviewed_library_exile=unviewed_library_exile,
            private_library_exile_viewer=private_library_exile_viewer, private_library_cast_source=private_library_cast_source,
            active_seats=self.host.active_seats, stack=self.state.stack, private_replacement_journal=private_replacement_journal,
        )
        validate_hidden_search_result_journal(card, destination, reveal_to=reveal_to, controller=controller,
                                              putting_player=putting_player, hidden_search_result_viewer=hidden_search_result_viewer)
        putting_player = entry_putting_player(self.host, card, destination, putting_player)
        if discard_effect_origin is not None:
            normalized_transition_kind_map(
                [(object_id, destination)], {object_id: transition_kind},
                discard_effect_origin=discard_effect_origin,
            )
        if card.permanent_composition is not None:
            from .composition_ordering import resolve_composition_departure
            moved=resolve_composition_departure(self,object_id,destination,reason=reason,position=position,
                replacement_selections=replacement_selections,transition_kind=transition_kind,
                prepared_replacement=prepared_replacement)
            return next((member for member in moved if member.object_id==object_id),card)
        validate_face_down_library_entry(card, destination, manifest_entry, cloak_entry)
        plan_or_card = self._prepare_move(
            card,
            destination,
            controller=controller,
            tapped=tapped,
            entry_pay_life=entry_pay_life,
            enter_face=enter_face,
            battle_protector=battle_protector,
            aura_target_ref=aura_target_ref,
            resolving_as_aura_spell=resolving_as_aura_spell,
            aura_enchant_spec=aura_enchant_spec,
            position=position,
            reason=reason,
            log=log,
            replacement_selections=replacement_selections,
            prepared_replacement=prepared_replacement,
            transition_kind=transition_kind,
            manifest_entry=manifest_entry,
            cloak_entry=cloak_entry,
            discard_effect_origin=discard_effect_origin,
            resolution_entry_enchantment=resolution_entry_enchantment,
        )
        if isinstance(plan_or_card, CardInstance):
            return plan_or_card
        plan = plan_resolution_entry_characteristics(plan_or_card, resolution_entry_subtype, resolution_entry_enchantment)
        if discard_effect_origin is not None and transition_kind is ZoneTransitionKind.DISCARD and plan.destination == "battlefield":
            putting_player = entry_putting_player(self.host, card, "battlefield", card.owner)
        prepared = plan.prepared_replacement
        if (prepared.devour_sacrifices or prepared.graveyard_copy_exiles or prepared.entry_land_discard) and not _devour_batch:
            from .zone_devour import commit_single_devour_entry
            return commit_single_devour_entry(self,plan,reason=reason,log=log,aura_target_ref=aura_target_ref,
                semantic_events=semantic_events, putting_player=putting_player)
        reveals = () if _devour_batch else prepare_zone_side_effects(self.host, (plan.prepared_replacement,))
        if plan.origin in {"battlefield", "graveyard"} and not characteristic_lki_prepared:
            self.pin_characteristic_departures((card,))
        departure = self._capture_departure(
            card,
            semantic_events=semantic_events,
            putting_player=putting_player,
        )
        self._commit_move(
            plan,
            departure,
            controller=controller,
            tapped=tapped,
            enter_face=plan.enter_face,
            aura_target_ref=aura_target_ref,
            zone_timestamp=zone_timestamp,
            reveal_to=tuple(reveal_to or ()),
            private_library_exile_viewer=private_library_exile_viewer,
            unviewed_library_exile=unviewed_library_exile,
            private_library_cast_source=private_library_cast_source,
        )
        finish_self_graveyard_shuffles(self, reveals)
        from .rules.cipher import reconcile_cipher_links
        reconcile_cipher_links(self.host)
        self._journal_move(
            plan,
            departure,
            reveal_to=tuple(reveal_to or ()),
            reason=reason,
            log=log,
            unviewed_library_exile=unviewed_library_exile and plan.destination == "exile",
        )
        self._complete_move(
            plan,
            departure,
            reason=reason,
            log=log,
            semantic_events=semantic_events,
            transition_kind=transition_kind,
            defer_control_sync=defer_control_sync, discard_effect_origin=discard_effect_origin,
            replacement_journal_viewer=(
                private_library_exile_viewer if private_replacement_journal else hidden_search_result_viewer
            ),
        )
        return card

    def _prepare_move(
        self,
        card: CardInstance,
        destination: str,
        *,
        controller: str | None,
        tapped: bool | None,
        entry_pay_life: bool,
        enter_face: str | None,
        battle_protector: str | None,
        aura_target_ref: str | None,
        resolving_as_aura_spell: bool,
        aura_enchant_spec: Any,
        position: str | int,
        reason: str,
        log: bool,
        replacement_selections: Sequence[str | None | Mapping[str, Any]],
        prepared_replacement: PreparedZoneChange | None,
        transition_kind: ZoneTransitionKind,
        manifest_entry: bool = False,
        cloak_entry: bool = False,
        discard_effect_origin: DiscardEffectOrigin | None = None,
        resolution_entry_enchantment: EntryEnchantmentSetting | None = None,
        meld_entry: Any = None,
    ) -> ZoneMovePlan | CardInstance:
        requested_destination = destination
        origin = card.zone
        library_face_down_entry = manifest_entry or cloak_entry
        if destination == "battlefield" and not library_face_down_entry:
            record = self.host.card_record(card)
            if (
                enter_face is None
                and getattr(record, "layout", None) == "transform"
                and getattr(record, "faces", ())
            ):
                enter_face = str(record.faces[0].get("name") or "") or None
            enter_face = trusted_daybound_entry_face(
                self.host,
                card,
                prospective_face=enter_face,
            )
        library_position = normalized_library_position(destination, position)
        if origin == requested_destination and origin not in {
            LIBRARY_ZONE,
            EXILE_ZONE,
            "command",
        }:
            return card
        origin_identity_public = origin in PUBLIC_ZONES and not card.face_down
        if (
            card.is_token
            and card.has_left_battlefield
            and origin not in {"battlefield", "outside"}
            and requested_destination not in {origin, "outside"}
        ):
            journal_prevented_token_move(self.host, card, origin, requested_destination, log=log)
            return card
        entry_characteristics, destination_type_line = prepare_move_entry_characteristics(
            self.host, card, enter_face, library_face_down_entry, destination, cloaked=cloak_entry, meld_entry=meld_entry,
            type_setting=resolution_entry_enchantment,
        )
        if (
            destination == "battlefield"
            and card.is_card_object
            and self.host._type_parts(destination_type_line)[0].intersection({"instant", "sorcery"})
        ):
            journal_prevented_nonpermanent_move(self.host, card, origin, requested_destination, log=log)
            return card
        if origin == LIBRARY_ZONE and requested_destination == LIBRARY_ZONE:
            self._reorder_library_card(
                card,
                library_position=library_position,
                reason=reason,
                log=log,
            )
            return card
        replacement = prepare_entry_replacement_for_move(self.host, card, destination, controller=controller,
            entry_characteristics=entry_characteristics, tapped=tapped, entry_pay_life=entry_pay_life,
            transition_kind=transition_kind, replacement_selections=replacement_selections,
            prepared_replacement=prepared_replacement, library_face_down_entry=library_face_down_entry, cloaked=cloak_entry,
            discard_effect_origin=discard_effect_origin)
        destination = replacement.destination
        copied, entry_characteristics, destination_type_line, aura_enchant_spec = entry_results.with_copy_entry_preview(
            self.host, card, replacement, entry_characteristics, destination_type_line,
            aura_enchant_spec, resolution_entry_enchantment,
        )
        prospective_protector = prospective_battle_entry_protector(
            destination=destination,
            entry_characteristics=entry_characteristics,
            controller=controller or card.owner,
            supplied_protector=(
                str(battle_protector)
                if battle_protector is not None
                else card.battle_protector
            ),
            active_seats=self.host.active_seats,
            error_type=GameRuleError,
        )
        aura_move = preflight_aura_zone_move(
            self.host,
            copied or card,
            destination=destination,
            requested_destination=requested_destination,
            destination_type_line=destination_type_line,
            enter_face=enter_face,
            enchant_spec=aura_enchant_spec,
            controller=controller,
            target_ref=aura_target_ref,
            resolving_as_spell=resolving_as_aura_spell,
            origin=origin,
            log=log,
            error_type=GameRuleError,
        )
        if aura_move.remain_in_origin:
            return card
        prepared_life_payment = prepare_move_entry_life_payment(self.host, replacement)
        from .face_down_characteristics_model import reveals_face_down_owner_hand_departure
        return ZoneMovePlan(
            card=card,
            requested_destination=requested_destination,
            destination=aura_move.destination,
            origin=origin,
            origin_identity_public=origin_identity_public,
            library_position=replacement_library_position(replacement, library_position),
            destination_type_line=destination_type_line,
            enter_face=enter_face,
            prepared_replacement=replacement,
            prepared_life_payment=prepared_life_payment,
            prospective_battle_protector=prospective_protector,
            aura_entry_plan=aura_move.entry_plan,
            manifest_entry=manifest_entry,
            cloak_entry=cloak_entry,
            reveal_face_down_departure=reveals_face_down_owner_hand_departure(self.state, card, aura_move.destination),
            meld_entry=meld_entry,
        )

    def _capture_departure(
        self,
        card: CardInstance,
        *,
        semantic_events: bool,
        putting_player: str | None = None,
    ) -> ZoneDepartureSnapshot:
        from .conditional_exile_model import source_conditional_bindings
        from .creature_power_damage import with_occurrence_power_departure
        attachments = tuple(
            self.state.cards[attachment_id].ref
            for attachment_id in card.attachments
            if attachment_id in self.state.cards
        )
        attached_to = attachment_target_ref(
            self.state.cards,
            self.state.players,
            card,
        )
        characteristics = (
            with_occurrence_power_departure(
                self.host, card,
                with_zone_event_numerics(self.host, card, _damage_source_characteristics(self.host, card)),
            )
            if semantic_events
            else {}
        )
        from .death_history_names import capture_departure_rules_names
        characteristics = capture_departure_rules_names(self.host, card, characteristics) if semantic_events else characteristics
        if card.zone == "battlefield":
            capture_explore_source_departure(self.host, card)
            from .connive import capture_connive_source_departure
            capture_connive_source_departure(self.host, card)
        return ZoneDepartureSnapshot(
            origin=card.zone,
            controller=card.controller,
            logical_object_id=card.logical_object_id,
            conditional_returns=source_conditional_bindings(card) if card.zone == 'battlefield' else (),
            characteristics=characteristics,
            counters=dict(card.counters) if semantic_events else None,
            putting_player=putting_player,
            attachments=attachments,
            attached_to=attached_to,
            trigger_sources=capture_departure_trigger_sources(
                self.host,
                semantic_events=semantic_events,
                origin=card.zone,
            ),
            copy_snapshot=(token_copy_snapshot(self.host, card)
                           if semantic_events and card.zone == "battlefield" else None),
            **stack_entry_cast_facts(card, include_multikicker_result=needs_multikicker_entry_fact(self.host, card)),
        )

    def _commit_move(
        self,
        plan: ZoneMovePlan,
        departure: ZoneDepartureSnapshot,
        *,
        controller: str | None,
        tapped: bool | None,
        enter_face: str | None,
        aura_target_ref: str | None,
        zone_timestamp: int | None,
        reveal_to: tuple[str, ...],
        already_detached: bool = False,
        private_library_exile_viewer: str | None = None,
        unviewed_library_exile: bool = False,
        private_library_cast_source: str | PrivateLibrarySearchPlaySource | None = None,
    ) -> None:
        card = plan.card
        shared_type_doors = self._prepared_entry_doors(plan, enter_face)
        if type(already_detached) is not bool:raise StateInvariantError('Detached membership flag must be boolean')
        if already_detached and any(card.object_id in ids for player in self.state.players.values() for ids in player.zones.values()):
            raise StateInvariantError('Detached physical object still has live zone membership')
        if departure.origin == 'hand' and plan.destination != 'hand':
            from .forecast import end_forecast_reveal
            end_forecast_reveal(self.host, card)
        if plan.prepared_life_payment is not None:
            try:
                commit_life_payment(
                    self.host,
                    plan.prepared_life_payment,
                )
            except LifeStateError as exc:
                raise GameRuleError(str(exc)) from exc
        if departure.origin == "graveyard":
            from .turn_history import record_graveyard_departure

            record_graveyard_departure(self.host, card, plan.destination)
        if departure.origin == "stack":
            self._depart_stack_object(card)
        elif not already_detached:
            self.remove_from_zone(card)
        self.reset_zone_change(
            card,
            plan.destination,
            zone_timestamp=zone_timestamp,
        )
        card.zone = plan.destination
        card.shared_type_doors = shared_type_doors
        if plan.library_face_down_entry and plan.destination == 'battlefield':
            mark_card_manifested(card, controller=controller or card.owner, cloaked=plan.cloak_entry)
        if enter_face is not None and plan.destination == "battlefield":
            card.active_face = enter_face
        if plan.destination == "battlefield":
            if plan.meld_entry is not None:
                from .meld_entry import commit_melded_components

                storage = commit_melded_components(self, card, plan.meld_entry)
                card.is_commander = storage.topology.is_commander
                card.permanent_composition = storage
                storage.validate_carrier(card)
            self._enter_battlefield(
                plan,
                controller=controller,
                tapped=tapped,
                aura_target_ref=aura_target_ref,
            )
        elif plan.destination == "outside":
            known = set(card.known_to)
            known.add(card.owner)
            card.known_to = sorted(known)
            card.revealed_to = sorted(
                viewer for viewer in set(card.revealed_to) if viewer in known
            )
        else:
            self._enter_owner_zone(
                plan,
                reveal_to=reveal_to,
            )
            apply_library_exile_visibility(
                card, plan.destination, viewer=private_library_exile_viewer,
                unviewed=unviewed_library_exile, cast_source=private_library_cast_source,
            )
            from .rules.linked_graveyard_play import committed_linked_graveyard_exile
            committed_linked_graveyard_exile(self.host, card, plan.prepared_replacement)

    def _prepared_entry_doors(self, plan, enter_face):
        if plan.destination != "battlefield" or plan.library_face_down_entry:
            return None
        from .shared_type_doors import shared_type_entry_doors

        return shared_type_entry_doors(
            self.state, plan.card, self.host.card_record(plan.card), enter_face=enter_face,
        )

    def _enter_battlefield(
        self,
        plan: ZoneMovePlan,
        *,
        controller: str | None,
        tapped: bool | None,
        aura_target_ref: str | None,
    ) -> None:
        card = plan.card
        card.controller = controller or card.owner
        self.host._require_seat(card.controller)
        card.tapped = plan.prepared_replacement.entry_tapped
        control_history.record_battlefield_acquisition(
            self.state,
            card,
            card.zone_timestamp,
        )
        card.entered_battlefield_turn_sequence = self.state.turn_sequence
        card.battle_protector = plan.prospective_battle_protector
        self.state.players[card.controller].zones["battlefield"].append(card.object_id)
        from .entry_results import commit_entry_designations
        commit_entry_designations(self.host, card, color=plan.prepared_replacement.entry_chosen_color,
                                  creature_type=plan.prepared_replacement.entry_chosen_creature_type,
                                  card_name=plan.prepared_replacement.entry_chosen_card_name,
                                  faction=plan.prepared_replacement.entry_chosen_faction,
                                  basic_land_type=plan.prepared_replacement.entry_chosen_basic_land_type,
                                  jeskai_temur_faction=plan.prepared_replacement.entry_chosen_jeskai_temur_faction)
        commit_aura_zone_move(
            self.host,
            card,
            plan.aura_entry_plan,
            error_type=GameRuleError,
        )
        pending_attachment = take_pending_attachment(card)
        if pending_attachment is not None:
            try:
                target = self.host._resolve_object(
                    card.controller,
                    pending_attachment.target_ref,
                    zones={pending_attachment.target_zone},
                )
            except GameRuleError:
                target = None
            if target is not None:
                attach_objects(
                    self.state.cards,
                    card,
                    target,
                    source_timestamp=self.next_timestamp(),
                    players=self.state.players,
                )
        if card.face_down:
            card.known_to = sorted({*card.known_to, card.controller})
            card.revealed_to = sorted(
                set(card.revealed_to).intersection(card.known_to)
            )
        else:
            card.known_to = list(self.host.seats)
            card.revealed_to = list(self.host.seats)
        self.host._refresh_world_supertype_timestamp(
            card,
            gained_at=card.zone_timestamp,
        )

    def _enter_owner_zone(
        self,
        plan: ZoneMovePlan,
        *,
        reveal_to: tuple[str, ...],
    ) -> None:
        card = plan.card
        owner_zone = self.state.players[card.owner].zones[plan.destination]
        if plan.destination == LIBRARY_ZONE:
            owner_zone.insert(
                self.library_insertion_index(
                    len(owner_zone),
                    plan.library_position,
                ),
                card.object_id,
            )
            card.known_to = [card.owner]
            card.revealed_to = []
            return
        owner_zone.append(card.object_id)
        if plan.destination in PUBLIC_ZONES:
            card.known_to = list(self.host.seats)
            card.revealed_to = list(self.host.seats)
            return
        known = {card.owner, *reveal_to}
        if plan.destination == "hand" and (plan.origin_identity_public or plan.reveal_face_down_departure):
            known.update(self.host.seats)
        card.known_to = sorted(known)
        card.revealed_to = sorted(set(reveal_to))

    def _journal_move(
        self,
        plan: ZoneMovePlan,
        departure: ZoneDepartureSnapshot,
        *,
        reveal_to: tuple[str, ...],
        reason: str,
        log: bool,
        unviewed_library_exile: bool = False,
    ) -> None:
        journal_zone_move(
            self.host,
            plan,
            departure,
            reveal_to=reveal_to,
            reason=reason,
            log=log,
            unviewed_library_exile=unviewed_library_exile,
        )

    def _complete_move(
        self,
        plan: ZoneMovePlan,
        departure: ZoneDepartureSnapshot,
        *,
        reason: str,
        log: bool,
        semantic_events: bool,
        transition_kind: ZoneTransitionKind,
        defer_control_sync: bool = False,
        discard_effect_origin: DiscardEffectOrigin | None = None,
        replacement_journal_viewer: str | None = None,
    ) -> None:
        card = plan.card
        journal_visibility = None
        if replacement_journal_viewer is not None and (card.face_down or card.zone not in PUBLIC_ZONES):
            journal_visibility = (replacement_journal_viewer, "analyst")
        log_applied_zone_replacements(
            self.host,
            plan.prepared_replacement,
            card,
            requested_destination=plan.requested_destination,
            visibility=journal_visibility,
            error_type=StateInvariantError,
        )
        entry_results.commit_prepared_entry_results(
            self.host,
            plan.prepared_replacement,
            card,
            reason=reason,
            log=log,
            error_type=StateInvariantError,
        )
        mark_intrinsic_entry_counters_initialized(
            card,
            destination=card.zone,
            destination_type_line=plan.destination_type_line,
        )
        if not defer_control_sync:
            from .control_effects import synchronize_control_effects
            synchronize_control_effects(self.host, reason="control duration source changed zones")
            from .soulbond_state import end_pairs_containing, reconcile_soulbond_pairs
            if departure.origin == 'battlefield' and plan.destination != 'battlefield':
                end_pairs_containing(self.host, (card.object_id,), reason='paired object left the battlefield')
            reconcile_soulbond_pairs(self.host)
        trigger_batch = resolved_library_exile_trigger_batch(self.host, ((card, departure),), deferred=defer_control_sync)
        if semantic_events:
            sources = departure.trigger_sources
            self.host._dispatch_zone_change_events(
                card,
                origin=departure.origin,
                destination=plan.destination,
                origin_controller=departure.controller,
                origin_logical_object_id=departure.logical_object_id,
                origin_data=departure.characteristics,
                origin_counters=departure.counters,
                origin_attachments=departure.attachments,
                origin_attached_to=departure.attached_to,
                departure_sources=sources.sources,
                departure_source_zones=sources.source_zones,
                departure_source_characteristics=(
                    sources.source_characteristics
                ),
                reason=reason,
                transition_kind=transition_kind,
                read_ahead_chapter=(
                    plan.prepared_replacement.read_ahead_chapter
                ),
                **({"discard_effect_origin": discard_effect_origin} if discard_effect_origin is not None else {}),
                **({"putting_player": departure.putting_player} if departure.putting_player is not None else {}),
                cast_option=departure.cast_option,
                paid_entry_copy=departure.paid_entry_copy,
                multikicker_paid=departure.multikicker_paid, declared_cast_costs=departure.declared_cast_costs if card.zone == 'battlefield' else None,
                trigger_batch=trigger_batch,
            )
        if not defer_control_sync:
            dispatch_counter_event_trees(self.host, (plan.prepared_replacement,), reason=reason,trigger_batch=trigger_batch)
            from .rules.state_triggers import observe_public_state_triggers
            observe_public_state_triggers(self.host,trigger_batch=trigger_batch)
            enqueue_trigger_batch(self.host,trigger_batch)
            from .conditional_exile_return import return_from_departure_snapshots
            return_from_departure_snapshots(self.host, ((card, departure),))

    def _reorder_library_card(
        self,
        card: CardInstance,
        *,
        library_position: str | int | None,
        reason: str,
        log: bool,
    ) -> None:
        library = self.state.players[card.owner].zones[LIBRARY_ZONE]
        if card.object_id not in library:
            raise GameRuleError("Library card is absent from its owner's library")
        library.remove(card.object_id)
        library.insert(
            self.library_insertion_index(len(library), library_position),
            card.object_id,
        )
        if log:
            self.host._log(
                card.owner,
                "library.reorder",
                f"{card.owner} changed a card's library position.",
                {
                    "position": library_position,
                    JOURNAL_REASON_FIELD: reason,
                },
                visibility=[card.owner, "analyst"],
                importance=1,
                changed_objects=[card.object_id],
                changed_players=[card.owner],
            )

    library_insertion_index = staticmethod(library_insertion_index)

    def dispatch_zone_change_events(
        self,
        card: CardInstance,
        *,
        departure: ZoneDepartureSnapshot,
        destination: str | None,
        reason: str,
        transition_kind: ZoneTransitionKind = ZoneTransitionKind.ORDINARY,
        read_ahead_chapter: int | None = None,
        trigger_batch: list[StackItem] | None = None,
        history_pre_recorded: bool = False,
        discard_effect_origin: DiscardEffectOrigin | None = None,
        discard_batch_member: bool = False,
    ) -> tuple[ZoneChangeOccurrence, list[StackItem], bool]:
        from .rules.city_blessing import synchronize_citys_blessing
        synchronize_citys_blessing(self.host,reason='zone transition before trigger facts')
        occurrence = self._zone_change_occurrence(
            card,
            departure=departure,
            destination=destination,
            reason=reason,
            transition_kind=transition_kind,
            read_ahead_chapter=read_ahead_chapter,
            discard_effect_origin=discard_effect_origin,
        )
        owns_trigger_batch = trigger_batch is None
        event_triggers = trigger_batch if trigger_batch is not None else []
        sources = departure.trigger_sources
        dispatch_zone_change_occurrence(
            self.host,
            occurrence,
            card,
            departure_sources=sources.sources,
            departure_source_zones=sources.source_zones,
            departure_source_characteristics=sources.source_characteristics,
            trigger_batch=event_triggers,
            history_pre_recorded=history_pre_recorded,
        )
        if occurrence.transition_kind is ZoneTransitionKind.DISCARD and not discard_batch_member:
            dispatch_committed_discard_groups(self.host, (occurrence,), trigger_batch=event_triggers)
        if owns_trigger_batch and occurrence.destination == "battlefield":
            synchronize_day_night(
                self.host,
                reason="bound permanent entered the battlefield",
                trigger_batch=event_triggers,
            )
        return occurrence, event_triggers, owns_trigger_batch

    def _zone_change_occurrence(
        self,
        card: CardInstance,
        *,
        departure: ZoneDepartureSnapshot,
        destination: str | None,
        reason: str,
        transition_kind: ZoneTransitionKind,
        read_ahead_chapter: int | None,
        discard_effect_origin: DiscardEffectOrigin | None = None,
    ) -> ZoneChangeOccurrence:
        return ZoneChangeOccurrence(
            object_id=card.object_id,
            card_ref=card.ref,
            owner=card.owner,
            origin=departure.origin,
            destination=destination or card.zone,
            previous_controller=departure.controller,
            current_controller=card.controller,
            previous_logical_object_id=departure.logical_object_id,
            current_logical_object_id=card.logical_object_id,
            zone_change_counter=card.zone_change_counter,
            token=card.is_token,
            card_object=card.is_card_object,
            previous_characteristics=departure.characteristics,
            current_characteristics=current_zone_event_characteristics(self.host,card),
            previous_counters=departure.counters,
            current_counters=dict(card.counters) if departure.counters is not None else None,
            schema_version=2 if departure.counters is not None else 1,
            previous_copy_snapshot=departure.copy_snapshot,
            current_copy_snapshot=(token_copy_snapshot(self.host, card)
                                   if card.zone == "battlefield" else None),
            previous_attachments=departure.attachments,
            previous_attached_to=departure.attached_to,
            tapped=card.tapped,
            cause=reason,
            transition_kind=transition_kind,
            read_ahead_chapter=read_ahead_chapter,
            cast_option=departure.cast_option if departure.origin == 'stack' and card.zone == 'battlefield' else None,
            paid_entry_copy=departure.paid_entry_copy if departure.origin == 'stack' and card.zone == 'battlefield' else None,
            multikicker_paid=departure.multikicker_paid if departure.origin == 'stack' and card.zone == 'battlefield' else None,
            declared_cast_costs=departure.declared_cast_costs if departure.origin == 'stack' and card.zone == 'battlefield' else None,
            source_entry_cast_information=self._source_entry_cast_information(card,departure),
            source_entry_x_information=self._source_entry_x_information(card,departure),
            paid_alternative_entry_information=self._paid_alternative_entry_information(card,departure),
            discard_effect_origin=discard_effect_origin,
            putting_player=departure.putting_player if card.zone == "battlefield" else None,
        )

    def _source_entry_cast_information(self,card,departure):
        from .rules.entry_cast_information import information_for_committed_entry
        return information_for_committed_entry(self.host,card,departure)

    def _paid_alternative_entry_information(self,card,departure):
        from .rules.paid_alternative_cast_facts import information_for_paid_entry
        return information_for_paid_entry(self.host,card,departure)

    def _source_entry_x_information(self,card,departure):
        from .rules.ravenous import ravenous_entry_information
        return ravenous_entry_information(self.host,card,departure)

    def move_cards_simultaneously(
        self,
        changes: Sequence[tuple[str, str]],
        *,
        reason: str,
        log: bool = False,
        tapped: bool | None = None,
        tapped_by_object: Mapping[str, bool] | None = None,
        entry_pay_life: Mapping[str, bool] | None = None,
        destination_controllers: Mapping[str, str] | None = None,
        effect_entry_counters: Mapping[str, Sequence[EffectEntryCounter]] | None = None,
        entry_characteristic_additions: Mapping[str, entry_results.EntryCharacteristicAddition] | None = None,
        aura_target_refs: Mapping[str, str] | None = None,
        replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
        transition_kinds: Mapping[str, ZoneTransitionKind] | None = None,
        prepared_replacements: Mapping[str,PreparedZoneChange] | None = None,
        entry_faces: Mapping[str,str] | None = None,
        publish_events: bool = True,
        damage_clears: Sequence[tuple[str, str]] = (),
        attacking_entries: Mapping[str, Any] | None = None,
        library_order: LibraryArrivalOrder | None = None,
        graveyard_orders=None,
        require_graveyard_order: bool = False,
        discard_effect_origin: DiscardEffectOrigin | None = None,
        putting_players: Mapping[str, str] | None = None,
        private_top_exile=None,
        simultaneous_life=None,
        battle_protectors: Mapping[str, str] | None = None,
    ) -> list[CardInstance]:
        if simultaneous_life is not None:
            from .life_change import validate_life_change_batch

            validate_life_change_batch(self.host, simultaneous_life)
        if private_top_exile is not None:
            from .rules.private_opponent_top_exile import PrivateTopExileBatch

            if not isinstance(private_top_exile, PrivateTopExileBatch):
                raise GameRuleError("Private exile requires its typed batch")
            private_top_exile.validate(self.host, changes)
        changes, transition_kinds = filter_prohibited_sacrifice_transitions(self.state, changes, transition_kinds)
        entry_players = batch_putting_players(self.host, changes, putting_players)
        entry_additions, tapped_entries = entry_results.prepare_simultaneous_entries(self.state, changes, entry_characteristic_additions, tapped_by_object)
        library_owner = validate_ordered_library_destination(
            self.host, self.state.cards, changes, library_order,
        ) if library_order is not None else None
        if attacking_entries:
            from .rules.attacking_card_entry import AttackingEntryRecipient

            entering = {object_id for object_id, destination in changes if destination == 'battlefield'}
            if any(object_id not in entering or not isinstance(recipient, AttackingEntryRecipient)
                for object_id, recipient in attacking_entries.items()):
                raise GameRuleError('Attacking entry requires typed recipients for the entering cards')
        if any(self.state.cards.get(object_id) is not None
                and self.state.cards[object_id].permanent_composition is not None for object_id,_ in changes):
            if graveyard_orders is not None or require_graveyard_order:
                raise GameRuleError("A composed departure needs its complete physical order")
            from .composition_ordering import resolve_composition_departure_batch
            return resolve_composition_departure_batch(self,changes,reason=reason,replacement_selections=replacement_selections,
                transition_kinds=transition_kinds,prepared_replacements=prepared_replacements,
                damage_clears=damage_clears)
        kinds = normalized_transition_kind_map(changes, transition_kinds, discard_effect_origin=discard_effect_origin)
        controllers = dict(destination_controllers or {})
        aura_targets = dict(aura_target_refs or {})
        faces=dict(entry_faces or {})
        if type(publish_events) is not bool or set(faces)-{oid for oid,_ in changes}:raise GameRuleError('Simultaneous entry options changed')
        validate_simultaneous_controllers(changes, controllers, aura_targets, self.host.active_seats)
        from . import land_entry_coordination
        land_entry_coordination.validate_batched_entry_life_requests(changes, prepared_replacements, entry_pay_life)
        if not changes and (effect_entry_counters or prepared_replacements or replacement_selections):
            raise GameRuleError('Empty zone transactions cannot carry per-object replacement inputs')
        if changes:
            changes,prepared,kinds,source_snapshot=self._prepare_simultaneous_zone_batch(changes,controllers,aura_targets,faces,
                tapped=tapped,effect_entry_counters=effect_entry_counters,replacement_selections=replacement_selections,
                kinds=kinds,prepared_replacements=prepared_replacements,log=log,tapped_by_object=tapped_entries,
                discard_effect_origin=discard_effect_origin, entry_pay_life=entry_pay_life,
                entry_characteristic_additions=entry_characteristic_additions,
                capture_entry_choices=battle_protectors is not None)
        if battle_protectors is not None:
            from .rules.batched_entry_choices import preflight_batch_protectors
            preflight_batch_protectors(self.host, changes, prepared if changes else {}, controllers, faces, battle_protectors)
        land_entry_coordination.preflight_batched_entry_life_payments(self.host, prepared if changes else {}, entry_pay_life)
        if changes and discard_effect_origin is not None and self.state.battlefield_entry_player_version is not None:
            entry_players.update({oid: self.state.cards[oid].owner for oid, result in prepared.items()
                if result.transition_kind is ZoneTransitionKind.DISCARD and result.destination == "battlefield"})
        if graveyard_orders is not None or require_graveyard_order:
            validate_prepared_graveyard_orders(
                graveyard_orders, prepared=prepared if changes else {}, cards=self.state.cards,
                apnap_order=self.host.apnap_order(),
            )
        if damage_clears:
            from .damage_results import clear_damage_for_permanents

            clear_damage_for_permanents(self.host, damage_clears)
        if not changes:
            if simultaneous_life is not None:
                from .effect_runtime.life_effects import commit_prepared_life_effect_changes

                commit_prepared_life_effect_changes(self.host, simultaneous_life)
            return []
        snapshots=self._commit_simultaneous_zone_batch(changes,prepared,kinds,controllers,aura_targets,faces,
            source_snapshot,reason=reason,log=log,entry_players=entry_players,private_top_exile=private_top_exile,
            battle_protectors=battle_protectors)
        if library_order is not None:
            commit_ordered_library_arrivals(
                self.state.players[library_owner].zones["library"], library_order,
                (card for card, _ in snapshots),
            )
        if graveyard_orders is not None:
            for group, refs in zip(graveyard_orders.groups, graveyard_orders.choices, strict=True):
                graveyard = self.state.players[group.owner].zones["graveyard"]
                graveyard[:] = place_ordered_graveyard_arrivals(graveyard, group, refs)
        entry_results.commit_entry_characteristic_additions(self.host, entry_additions)
        return self._publish_simultaneous_zone_batch(snapshots,prepared,kinds,reason=reason,
            publish_events=publish_events,attacking_entries=attacking_entries, discard_effect_origin=discard_effect_origin,
            simultaneous_life=simultaneous_life)

    def _commit_simultaneous_zone_batch(self,changes,prepared,kinds,controllers,aura_targets,faces,source_snapshot,*,reason,log,destination_timestamp=None,entry_players=None,private_top_exile=None,battle_protectors=None):
        """Commit an already validated group without publishing partial facts."""
        reveals = prepare_zone_side_effects(self.host, prepared.values())
        snapshots = tuple(
            capture_simultaneous_departure(
                self.host, self.state.cards[object_id],
                source_snapshot=source_snapshot,
                putting_player=(entry_players or {}).get(object_id),
            )
            for object_id, _destination in changes
        )
        self.pin_characteristic_departures(
            tuple(snapshot[0] for snapshot in snapshots)
        )
        destination_timestamp = self.next_timestamp() if destination_timestamp is None else destination_timestamp
        for object_id, destination in changes:
            self.move_card(
                object_id,
                destination,
                zone_timestamp=destination_timestamp,
                controller=controllers.get(object_id),
                aura_target_ref=aura_targets.get(object_id),
                battle_protector=(battle_protectors or {}).get(object_id),
                tapped=prepared[object_id].requested_tapped,
                entry_pay_life=prepared[object_id].requested_entry_pay_life,
                enter_face=faces.get(object_id),
                reason=reason,
                log=log,
                semantic_events=False,
                prepared_replacement=prepared[object_id],
                characteristic_lki_prepared=True,
                transition_kind=kinds.get(object_id, ZoneTransitionKind.ORDINARY),
                defer_control_sync=True,
                _devour_batch=True,
                **({"unviewed_library_exile": True} if private_top_exile is not None else {}),
            )
        if private_top_exile is not None:
            for card, _ in snapshots:
                private_top_exile.grant_committed(card)
        finish_self_graveyard_shuffles(self, reveals)
        return snapshots

    def _publish_simultaneous_zone_batch(
        self, snapshots, prepared, kinds, *, reason, publish_events=True,
        attacking_entries=None, discard_effect_origin=None, simultaneous_life=None,
    ):
        """Seal history and discover triggers after every group member commits."""
        if simultaneous_life is not None:
            from .life_change import commit_life_change_batch

            commit_life_change_batch(self.host, simultaneous_life, dispatch_events=False)
        from .control_effects import synchronize_control_effects
        synchronize_control_effects(self.host, reason="control duration sources changed zones simultaneously")
        from .rules.city_blessing import synchronize_citys_blessing
        synchronize_citys_blessing(self.host,reason='simultaneous zone transitions before trigger facts')
        from .soulbond_state import end_pairs_containing, reconcile_soulbond_pairs
        end_pairs_containing(self.host,
            (card.object_id for card, departure in snapshots if departure.origin == 'battlefield'
             and card.zone != 'battlefield'), reason='paired objects left simultaneously')
        reconcile_soulbond_pairs(self.host)
        if attacking_entries:
            from .rules.attacking_card_entry import commit_attacking_entry

            for card, _departure in snapshots:
                if card.object_id in attacking_entries:
                    commit_attacking_entry(self.host, card, attacking_entries[card.object_id])
        occurrences = tuple(
            self._zone_change_occurrence(
                card,
                departure=departure,
                destination=card.zone,
                reason=reason,
                transition_kind=kinds.get(
                    card.object_id,
                    ZoneTransitionKind.ORDINARY,
                ),
                read_ahead_chapter=prepared[
                    card.object_id
                ].read_ahead_chapter,
                discard_effect_origin=(discard_effect_origin
                    if kinds.get(card.object_id) is ZoneTransitionKind.DISCARD else None),
            )
            for card, departure in snapshots
        )
        for occurrence in occurrences:
            record_zone_change_history(self.host, occurrence)
        trigger_batch = resolved_library_exile_trigger_batch(self.host, snapshots)
        dispatch_counter_event_trees(self.host, tuple(prepared.values()), reason=reason, trigger_batch=trigger_batch)
        if publish_events:
            dispatch_committed_batch_zone_events(self.host, snapshots, occurrences, trigger_batch=trigger_batch)
        if any(card.zone == "battlefield" for card, _departure in snapshots):
            synchronize_day_night(
                self.host,
                reason="simultaneous bound permanents entered the battlefield",
                trigger_batch=trigger_batch,
            )
        from .rules.state_triggers import observe_public_state_triggers
        observe_public_state_triggers(self.host,trigger_batch=trigger_batch)
        enqueue_trigger_batch(self.host, trigger_batch)
        if simultaneous_life is not None:
            from .player_result_events import dispatch_life_gain_records, dispatch_life_loss_records

            dispatch_life_loss_records(self.host, simultaneous_life.records)
            dispatch_life_gain_records(self.host, simultaneous_life.records)
        from .conditional_exile_return import return_from_departure_snapshots
        return_from_departure_snapshots(self.host, snapshots)
        return [card for card, _departure in snapshots]

    def _prepare_simultaneous_zone_batch(self,changes,controllers,aura_targets,faces,*,tapped,
            effect_entry_counters,replacement_selections,kinds,prepared_replacements,log,tapped_by_object=None,
            discard_effect_origin=None,entry_pay_life=None,entry_characteristic_additions=None,capture_entry_choices=False):
        """Capture sources and validate every entry/sacrifice plan before movement."""
        entry_characteristics = {
            object_id: entry_results.prospective_entry_characteristics(
                self.host, card=self.state.cards[object_id], enter_face=faces.get(object_id),
                addition=(entry_characteristic_additions or {}).get(object_id),
            )
            for object_id, destination in changes
            if destination == "battlefield"
        }
        source_snapshot = capture_zone_trigger_snapshot(self.host, self.semantic_event_sources())
        sources = source_snapshot.sources
        prepared = prepare_zone_change_replacement_batch(
            self.host,
            tuple(changes),
            requested_tapped={
                object_id: (tapped_by_object or {}).get(object_id, bool(tapped) if tapped is not None else False)
                for object_id, _destination in changes
            },
            destination_controllers={
                object_id: controllers[object_id]
                for object_id, destination in changes
                if destination == "battlefield" and object_id in controllers
            },
            entry_characteristics=entry_characteristics,
            effect_entry_counters=effect_entry_counters,
            entry_pay_life=entry_pay_life,
            sources=sources,
            source_zones=source_snapshot.source_zones,
            selections=tuple(replacement_selections),
            transition_kinds=kinds,
            error_type=GameRuleError,
            discard_effect_origin=discard_effect_origin,
        ) if prepared_replacements is None else dict(prepared_replacements)
        if set(prepared)!={oid for oid,_ in changes}:raise GameRuleError('Prepared simultaneous zone plans do not match their objects')
        from .rules.batched_entry_choices import preflight_batch_auras
        changes = preflight_batch_auras(self.host, changes, prepared, entry_characteristics, controllers, aura_targets, log,
                                        capture_choices=capture_entry_choices)
        if not changes:return (),prepared,kinds,source_snapshot
        prepared={object_id:prepared[object_id] for object_id,_destination in changes}
        from .zone_devour import expand_devour_zone_plans
        from .rules.graveyard_entry_copy import expand_graveyard_copy_zone_plans
        changes,prepared,kinds=expand_devour_zone_plans(self.host,changes,prepared,kinds)
        changes,prepared,kinds=expand_graveyard_copy_zone_plans(self.host,changes,prepared,kinds)
        from .semantic_runtime.entry_cost_preparation import expand_entry_land_discards

        changes,prepared,kinds=expand_entry_land_discards(self.host,changes,prepared,kinds,error_type=GameRuleError)
        for oid,destination in changes:
            plan=prepared[oid]
            prepare_zone_change_replacement(self.host,self.state.cards[oid],destination,
                destination_controller=controllers.get(oid),entry_characteristics=entry_characteristics.get(oid),
                requested_tapped=plan.requested_tapped,entry_pay_life=plan.requested_entry_pay_life,
                transition_kind=kinds.get(oid,ZoneTransitionKind.ORDINARY),prepared=plan,error_type=GameRuleError)
        return changes,prepared,kinds,source_snapshot

    def move_exiled_cards_to_library_bottom_random(
        self,
        object_ids: Sequence[str],
        *,
        owner: str,
        randomization_key: str,
        reason: str,
        replacement_selections: Sequence[str | Mapping[str,Any]]=(),
    ) -> tuple[CardInstance, ...]:
        """Move one public exile group to its owner's library bottom at random."""

        self.host._require_seat(owner)
        if type(randomization_key) is not str or not randomization_key:
            raise GameRuleError(
                "Random-bottom movement requires an identity key"
            )
        normalized = tuple(object_ids)
        if any(
            type(object_id) is not str or not object_id
            for object_id in normalized
        ):
            raise GameRuleError(
                "Random-bottom object identities must be nonempty strings"
            )
        if len(normalized) != len(set(normalized)):
            raise GameRuleError("Random-bottom object identities must be unique")
        cards = tuple(
            self.state.cards.get(object_id) for object_id in normalized
        )
        if any(
            card is None or card.owner != owner or card.zone != EXILE_ZONE
            for card in cards
        ):
            raise GameRuleError(
                "Random-bottom movement requires current exiled cards of one owner"
            )
        if not normalized:
            return ()
        ordered = list(normalized)
        random.Random(
            f"{self.state.config.seed}|{owner}|library-bottom-random|"
            f"{randomization_key}"
        ).shuffle(ordered)
        moved = tuple(
            self.move_cards_simultaneously(
                [(object_id, LIBRARY_ZONE) for object_id in ordered],
                reason=reason,
                log=False,
                replacement_selections=replacement_selections,
            )
        )
        library = self.state.players[owner].zones[LIBRARY_ZONE]
        bottom = [
            object_id
            for object_id in ordered
            if self.state.cards[object_id].zone == LIBRARY_ZONE
            and self.state.cards[object_id].owner == owner
        ]
        bottom_set = set(bottom)
        library[:] = [
            *bottom,
            *(object_id for object_id in library if object_id not in bottom_set),
        ]
        self.host._log(
            owner,
            "library.bottom.random",
            f"{owner} put {len(bottom)} card(s) on the bottom at random.",
            {"count": len(bottom), JOURNAL_REASON_FIELD: reason},
            importance=2,
            changed_objects=bottom,
            changed_players=[owner],
        )
        return moved

    def reveal_library_search_results(
        self, seat: str, object_ids: Sequence[str], *, public: bool,
        expected_zone: str = LIBRARY_ZONE, announce_public_results: bool = False,
    ) -> None:
        """Retain found-card knowledge before a search's shuffle or placement."""
        self.host._require_seat(seat)
        cards = validated_library_search_results(self.state, seat, object_ids, public=public,
            expected_zone=expected_zone, announce=announce_public_results)
        self._reveal_library_result_cards(seat, cards, public=public)
        if public and cards:
            publish_library_search_reveal(self.host, seat, cards, announce=announce_public_results)

    def reveal_selected_library_cards(self, seat: str, object_ids: Sequence[str]) -> None:
        """Reveal the selected physical cards without revealing their library order."""
        self.host._require_seat(seat, in_game=True)
        cards = validated_library_search_results(self.state, seat, object_ids, public=True,
                                                  expected_zone=LIBRARY_ZONE, announce=True)
        if cards:
            shown = [{"ref": card.ref, "name": card.printed_name} for card in sorted(cards, key=lambda card: card.ref)]
            self.host._log(seat, "library.reveal", f"{seat} revealed {', '.join(row['name'] for row in shown)}.",
                           {"player": seat, "cards": shown}, changed_objects=object_ids, changed_players=[seat])

    def _reveal_library_result_cards(self, seat, cards, *, public):
        viewers = set(self.host.seats) if public else {seat}
        for card in cards:
            card.known_to = sorted(set(card.known_to).union(viewers))
            if public:
                card.revealed_to = sorted(set(card.revealed_to).union(viewers))

    def shuffle_library(
        self, seat: str, *, reason: str = "shuffle",
        excluded_object_ids: Sequence[str] = (),
        actor: str | None = None,
        shuffling_player: str | None = None,
    ) -> None:
        self.host._require_seat(seat)
        if shuffling_player not in (None, seat, actor):
            self.host._require_seat(shuffling_player)
        if actor is None:
            actor = seat
        elif actor != seat:
            self.host._require_seat(actor)
        player = self.state.players[seat]
        excluded = set(excluded_object_ids)
        if len(excluded) != len(excluded_object_ids) or any(
            object_id not in player.zones[LIBRARY_ZONE]
            or self.state.cards[object_id].owner != seat
            or self.state.cards[object_id].zone != LIBRARY_ZONE
            for object_id in excluded
        ):
            raise GameRuleError("Shuffle exclusions must be unique current library objects")
        count = int(player.stats.get("shuffle_count", 0)) + 1
        player.stats["shuffle_count"] = count
        randomizer = random.Random(
            f"{self.state.config.seed}|{seat}|shuffle|{count}"
        )
        shuffled = [
            object_id for object_id in player.zones[LIBRARY_ZONE]
            if object_id not in excluded
        ]
        randomizer.shuffle(shuffled)
        remaining = iter(shuffled)
        player.zones[LIBRARY_ZONE][:] = [
            object_id if object_id in excluded else next(remaining)
            for object_id in player.zones[LIBRARY_ZONE]
        ]
        for object_id in shuffled:
            card = self.state.cards[object_id]
            card.known_to = []
            card.revealed_to = []
        self.host._log(
            actor,
            "library.shuffle",
            f"{seat} shuffled." if actor == seat else f"{actor} shuffled {seat}'s library.",
            {JOURNAL_REASON_FIELD: reason, "count": count},
            importance=0,
            changed_players=[seat],
        )
        from .rules.opponent_library_shuffle import dispatch_committed_library_shuffle

        if shuffling_player in (None, seat):
            dispatch_committed_library_shuffle(self.host, seat, count)


__all__ = [
    "ZoneDepartureSnapshot",
    "ZoneMovePlan",
    "ZoneTransitionHost",
    "ZoneTransitionOwner",
]
