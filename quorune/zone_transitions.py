from __future__ import annotations

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
from .attachments import (
    attach_objects,
    attachment_target_ref,
    clear_object_attachment_relations,
    take_pending_attachment,
)
from .aura import commit_aura_zone_move, preflight_aura_zone_move
from .entry_counters import (
    capture_prospective_entry_characteristics,
    mark_intrinsic_entry_counters_initialized,
    prospective_battle_entry_protector,
)
from .entry_results import commit_prepared_entry_results
from .errors import GameRuleError, StateInvariantError
from .life_state import (
    LifeStateError,
    commit_life_payment,
    prepare_life_payment,
)
from .kicker import KICKER_ANNOTATION
from .model import CardInstance, GameState, StackItem
from .relative_power_target import pin_host_relative_power_source_departures
from .station import pin_host_station_departures
from .semantic_runtime import (
    PreparedZoneChange,
    log_applied_zone_replacements,
    prepare_zone_change_replacement,
    prepare_zone_change_replacement_batch,
)
from .semantic_runtime.explore import capture_explore_source_departure
from .trigger_processing import enqueue_trigger_batch
from .zone_object_state import reset_card_after_zone_change
from .zone_trigger_events import (
    ZoneChangeOccurrence,
    ZoneTransitionKind,
    normalized_library_position,
    normalized_transition_kind_map,
    validate_zone_transition_request,
)
from .zone_trigger_processing import (
    DepartureTriggerSnapshot,
    capture_departure_trigger_sources,
    dispatch_zone_change_occurrence,
    semantic_event_sources,
)
from .day_night import synchronize_day_night
from .permanent_transform import trusted_daybound_entry_face
from .zone_transition_model import (
    EXILE_ZONE,
    JOURNAL_REASON_FIELD,
    LIBRARY_ZONE,
    PUBLIC_ZONES,
    ZoneDepartureSnapshot,
    ZoneMovePlan,
    ZoneTransitionHost,
)
from .zone_transition_journal import journal_zone_move


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

    def pin_characteristic_departures(
        self,
        cards: Sequence[CardInstance],
    ) -> None:
        """Capture pending characteristic LKI as one rollback-safe update."""

        contexts = tuple(
            (item, copy.deepcopy(item.context))
            for item in self.state.stack
        )
        try:
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
        except StateInvariantError:
            for item, context in contexts:
                item.context = context
            raise

    def semantic_event_sources(self, *, zones: set[str] | None = None) -> list[CardInstance]:
        return semantic_event_sources(
            self.state.cards.values(),
            active_seats=self.host.active_seats,
            zones=zones,
        )

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

    def reset_zone_change(
        self,
        card: CardInstance,
        destination: str,
        *,
        zone_timestamp: int | None = None,
    ) -> None:
        origin = card.zone
        creates_new_object = (
            origin != destination or origin in {EXILE_ZONE, "command"}
        )
        if not creates_new_object:
            return
        self.host._remove_object_from_combat(
            card,
            reason=f"zone change to {destination}",
        )
        stack_to_battlefield = origin == "stack" and destination == "battlefield"
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
    ) -> CardInstance:
        card = validate_zone_transition_request(
            self.state.cards,
            object_id,
            destination,
            transition_kind,
        )
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
        )
        if isinstance(plan_or_card, CardInstance):
            return plan_or_card
        plan = plan_or_card
        if plan.origin == "battlefield" and not characteristic_lki_prepared:
            self.pin_characteristic_departures((card,))
        departure = self._capture_departure(
            card,
            semantic_events=semantic_events,
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
        )
        self._journal_move(
            plan,
            departure,
            reveal_to=tuple(reveal_to or ()),
            reason=reason,
            log=log,
        )
        self._complete_move(
            plan,
            departure,
            reason=reason,
            log=log,
            semantic_events=semantic_events,
            transition_kind=transition_kind,
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
    ) -> ZoneMovePlan | CardInstance:
        requested_destination = destination
        origin = card.zone
        if destination == "battlefield":
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
            self._log_prevented_token(card, origin, requested_destination, log=log)
            return card
        entry_characteristics, destination_type_line = capture_prospective_entry_characteristics(
            self.host,
            card=card,
            enter_face=enter_face,
        )
        if (
            destination == "battlefield"
            and card.is_card_object
            and self.host._type_parts(destination_type_line)[0].intersection({"instant", "sorcery"})
        ):
            self._log_prevented_nonpermanent(card, origin, requested_destination, log=log)
            return card
        if origin == LIBRARY_ZONE and requested_destination == LIBRARY_ZONE:
            self._reorder_library_card(
                card,
                library_position=library_position,
                reason=reason,
                log=log,
            )
            return card
        replacement = prepare_zone_change_replacement(
            self.host,
            card,
            destination,
            destination_controller=controller,
            entry_characteristics=entry_characteristics,
            requested_tapped=bool(tapped) if tapped is not None else False,
            entry_pay_life=entry_pay_life,
            selections=tuple(replacement_selections),
            prepared=prepared_replacement,
            error_type=GameRuleError,
        )
        destination = replacement.destination
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
            card,
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
        prepared_life_payment = None
        if replacement.entry_life_payment:
            if replacement.destination != "battlefield" or (
                replacement.destination_controller is None
            ):
                raise GameRuleError(
                    "Entry life payments require a battlefield controller"
                )
            try:
                prepared_life_payment = prepare_life_payment(
                    self.host,
                    replacement.destination_controller,
                    replacement.entry_life_payment,
                )
            except LifeStateError as exc:
                raise GameRuleError(str(exc)) from exc
        return ZoneMovePlan(
            card=card,
            requested_destination=requested_destination,
            destination=aura_move.destination,
            origin=origin,
            origin_identity_public=origin_identity_public,
            library_position=library_position,
            destination_type_line=destination_type_line,
            enter_face=enter_face,
            prepared_replacement=replacement,
            prepared_life_payment=prepared_life_payment,
            prospective_battle_protector=prospective_protector,
            aura_entry_plan=aura_move.entry_plan,
        )

    def _capture_departure(
        self,
        card: CardInstance,
        *,
        semantic_events: bool,
    ) -> ZoneDepartureSnapshot:
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
            copy.deepcopy(self.host._effective_card_data(card))
            if semantic_events
            else {}
        )
        if card.zone == "battlefield":
            capture_explore_source_departure(self.host, card)
        return ZoneDepartureSnapshot(
            origin=card.zone,
            controller=card.controller,
            logical_object_id=card.logical_object_id,
            characteristics=characteristics,
            attachments=attachments,
            attached_to=attached_to,
            trigger_sources=capture_departure_trigger_sources(
                self.host,
                semantic_events=semantic_events,
                origin=card.zone,
            ),
            cast_option=(
                "kicked"
                if card.annotations.get(KICKER_ANNOTATION) is True
                else None
            ),
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
    ) -> None:
        card = plan.card
        if plan.prepared_life_payment is not None:
            try:
                commit_life_payment(
                    self.host,
                    plan.prepared_life_payment,
                )
            except LifeStateError as exc:
                raise GameRuleError(str(exc)) from exc
        if departure.origin == "stack":
            if not any(
                item.card_object_id == card.object_id
                and item.context.get("currently_resolving")
                for item in self.state.stack
            ):
                self.state.stack[:] = [
                    item
                    for item in self.state.stack
                    if item.card_object_id != card.object_id
                ]
        else:
            self.remove_from_zone(card)
        self.reset_zone_change(
            card,
            plan.destination,
            zone_timestamp=zone_timestamp,
        )
        card.zone = plan.destination
        if enter_face is not None:
            card.active_face = enter_face
        if plan.destination == "battlefield":
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
        if plan.destination == "hand" and plan.origin_identity_public:
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
    ) -> None:
        journal_zone_move(
            self.host,
            plan,
            departure,
            reveal_to=reveal_to,
            reason=reason,
            log=log,
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
    ) -> None:
        card = plan.card
        log_applied_zone_replacements(
            self.host,
            plan.prepared_replacement,
            card,
            requested_destination=plan.requested_destination,
            error_type=StateInvariantError,
        )
        commit_prepared_entry_results(
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
        if semantic_events:
            sources = departure.trigger_sources
            self.host._dispatch_zone_change_events(
                card,
                origin=departure.origin,
                destination=plan.destination,
                origin_controller=departure.controller,
                origin_logical_object_id=departure.logical_object_id,
                origin_data=departure.characteristics,
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
            )

    def _log_prevented_token(
        self,
        card: CardInstance,
        origin: str,
        destination: str,
        *,
        log: bool,
    ) -> None:
        if log:
            self.host._log(
                None,
                "zone.move.prevented",
                f"{card.ref} remained in {origin}; a token that left the battlefield cannot move again.",
                {
                    "object": card.ref,
                    "from": origin,
                    "requested_destination": destination,
                    "rule": "111.8",
                },
                importance=2,
                changed_objects=[card.object_id],
                changed_players=[card.owner],
            )

    def _log_prevented_nonpermanent(
        self,
        card: CardInstance,
        origin: str,
        destination: str,
        *,
        log: bool,
    ) -> None:
        if log:
            self.host._log(
                None,
                "zone.move.prevented",
                f"{card.ref} remained in {origin}; an instant or sorcery card cannot enter the battlefield.",
                {
                    "object": card.ref,
                    "from": origin,
                    "requested_destination": destination,
                    "rule": "400.4a",
                },
                importance=2,
                changed_objects=[card.object_id],
                changed_players=[card.owner],
            )

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

    @staticmethod
    def library_insertion_index(library_size: int, position: str | int | None) -> int:
        if position == "top":
            return library_size
        if position == "bottom":
            return 0
        if isinstance(position, int):
            return max(0, library_size - position + 1)
        raise GameRuleError("Validated library position is required")

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
    ) -> tuple[ZoneChangeOccurrence, list[StackItem], bool]:
        occurrence = ZoneChangeOccurrence(
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
            current_characteristics=self.host._effective_card_data(card),
            previous_attachments=departure.attachments,
            previous_attached_to=departure.attached_to,
            tapped=card.tapped,
            cause=reason,
            transition_kind=transition_kind,
            read_ahead_chapter=read_ahead_chapter,
            cast_option=departure.cast_option,
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
        )
        if owns_trigger_batch and occurrence.destination == "battlefield":
            synchronize_day_night(
                self.host,
                reason="bound permanent entered the battlefield",
                trigger_batch=event_triggers,
            )
        return occurrence, event_triggers, owns_trigger_batch

    def move_cards_simultaneously(
        self,
        changes: Sequence[tuple[str, str]],
        *,
        reason: str,
        log: bool = False,
        tapped: bool | None = None,
        replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
        transition_kinds: Mapping[str, ZoneTransitionKind] | None = None,
    ) -> list[CardInstance]:
        kinds = normalized_transition_kind_map(changes, transition_kinds)
        sources = tuple(copy.deepcopy(source) for source in self.semantic_event_sources())
        source_snapshot = DepartureTriggerSnapshot(
            sources=sources,
            source_zones={source.object_id: source.zone for source in sources},
            source_characteristics={
                source.object_id: copy.deepcopy(self.host._effective_card_data(source))
                for source in sources
            },
        )
        prepared = prepare_zone_change_replacement_batch(
            self.host,
            tuple(changes),
            requested_tapped={
                object_id: bool(tapped) if tapped is not None else False
                for object_id, _destination in changes
            },
            sources=sources,
            source_zones=source_snapshot.source_zones,
            selections=tuple(replacement_selections),
            error_type=GameRuleError,
        )
        snapshots = tuple(
            self._simultaneous_departure(
                self.state.cards[object_id],
                source_snapshot=source_snapshot,
            )
            for object_id, _destination in changes
        )
        self.pin_characteristic_departures(
            tuple(snapshot[0] for snapshot in snapshots)
        )
        destination_timestamp = self.next_timestamp()
        for object_id, destination in changes:
            self.move_card(
                object_id,
                destination,
                zone_timestamp=destination_timestamp,
                tapped=tapped,
                reason=reason,
                log=log,
                semantic_events=False,
                prepared_replacement=prepared[object_id],
                characteristic_lki_prepared=True,
                transition_kind=kinds.get(object_id, ZoneTransitionKind.ORDINARY),
            )
        trigger_batch: list[StackItem] = []
        for card, departure in snapshots:
            sources = departure.trigger_sources
            self.host._dispatch_zone_change_events(
                card,
                origin=departure.origin,
                destination=card.zone,
                origin_controller=departure.controller,
                origin_logical_object_id=departure.logical_object_id,
                origin_data=departure.characteristics,
                origin_attachments=departure.attachments,
                origin_attached_to=departure.attached_to,
                departure_sources=sources.sources,
                departure_source_zones=sources.source_zones,
                departure_source_characteristics=(
                    sources.source_characteristics
                ),
                reason=reason,
                transition_kind=kinds.get(card.object_id, ZoneTransitionKind.ORDINARY),
                read_ahead_chapter=(
                    prepared[card.object_id].read_ahead_chapter
                ),
                trigger_batch=trigger_batch,
            )
        if any(card.zone == "battlefield" for card, _departure in snapshots):
            synchronize_day_night(
                self.host,
                reason="simultaneous bound permanents entered the battlefield",
                trigger_batch=trigger_batch,
            )
        enqueue_trigger_batch(self.host, trigger_batch)
        return [card for card, _departure in snapshots]

    def _simultaneous_departure(
        self,
        card: CardInstance,
        *,
        source_snapshot: DepartureTriggerSnapshot,
    ) -> tuple[CardInstance, ZoneDepartureSnapshot]:
        return (
            card,
            ZoneDepartureSnapshot(
                origin=card.zone,
                controller=card.controller,
                logical_object_id=card.logical_object_id,
                characteristics=copy.deepcopy(self.host._effective_card_data(card)),
                attachments=tuple(
                    self.state.cards[attachment_id].ref
                    for attachment_id in card.attachments
                    if attachment_id in self.state.cards
                ),
                attached_to=attachment_target_ref(
                    self.state.cards,
                    self.state.players,
                    card,
                ),
                trigger_sources=source_snapshot,
            ),
        )

    def move_exiled_cards_to_library_bottom_random(
        self,
        object_ids: Sequence[str],
        *,
        owner: str,
        randomization_key: str,
        reason: str,
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

    def shuffle_library(self, seat: str, *, reason: str = "shuffle") -> None:
        self.host._require_seat(seat)
        player = self.state.players[seat]
        count = int(player.stats.get("shuffle_count", 0)) + 1
        player.stats["shuffle_count"] = count
        randomizer = random.Random(
            f"{self.state.config.seed}|{seat}|shuffle|{count}"
        )
        randomizer.shuffle(player.zones[LIBRARY_ZONE])
        for object_id in player.zones[LIBRARY_ZONE]:
            card = self.state.cards[object_id]
            card.known_to = []
            card.revealed_to = []
        self.host._log(
            seat,
            "library.shuffle",
            f"{seat} shuffled.",
            {JOURNAL_REASON_FIELD: reason, "count": count},
            importance=0,
            changed_players=[seat],
        )


__all__ = [
    "ZoneDepartureSnapshot",
    "ZoneMovePlan",
    "ZoneTransitionHost",
    "ZoneTransitionOwner",
]
