from __future__ import annotations

"""Typed boundary values for authoritative zone transitions."""

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence
from .conditional_exile_model import ConditionalExileBinding
from .errors import GameRuleError
from .self_graveyard_exile_model import SELF_GRAVEYARD_SHUFFLE_RESULT

from .model import CardInstance, GameState
from .life_state import PreparedLifePayment
from .semantic_runtime import PreparedZoneChange
from .zone_trigger_processing import DepartureTriggerSnapshot


EXILE_ZONE = "exile"
LIBRARY_ZONE = "library"


def replacement_library_position(prepared: PreparedZoneChange, position: str | int | None) -> str | int | None:
    if prepared.destination == LIBRARY_ZONE and SELF_GRAVEYARD_SHUFFLE_RESULT in prepared.event.payload:
        # The card joins the library before the selected replacement shuffles it.
        return "bottom"
    return position


def library_insertion_index(library_size: int, position: str | int | None) -> int:
    if position == "top":
        return library_size
    if position == "bottom":
        return 0
    if isinstance(position, int):
        return max(0, library_size - position + 1)
    raise GameRuleError("Validated library position is required")
JOURNAL_REASON_FIELD = "reason"
PUBLIC_ZONES = frozenset(
    {"battlefield", "graveyard", EXILE_ZONE, "command", "stack"}
)
HIDDEN_ZONES = frozenset({"hand", LIBRARY_ZONE})


class ZoneTransitionHost(Protocol):
    state: GameState
    seats: tuple[str, ...]
    active_seats: list[str]

    def _require_seat(self, seat: str, *, in_game: bool = False) -> None: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    def _effective_card_data(
        self,
        card: CardInstance,
    ) -> Mapping[str, Any]: ...

    def _type_parts(
        self,
        type_line: str,
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _remove_object_from_combat(
        self,
        card: CardInstance,
        *,
        reason: str,
    ) -> None: ...

    def _refresh_world_supertype_timestamp(
        self,
        card: CardInstance,
        *,
        gained_at: int | None = None,
    ) -> None: ...

    def _resolve_object(
        self,
        seat: str,
        value: str,
        **kwargs: Any,
    ) -> CardInstance: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, kind: str, ref: str) -> str: ...

    def _semantic_event_sources(
        self,
        *,
        zones: set[str] | None = None,
    ) -> list[CardInstance]: ...

    def _dispatch_semantic_event(
        self,
        event: str,
        context: Mapping[str, Any],
        **kwargs: Any,
    ) -> list[str]: ...

    def _record_turn_history(self, kind: str, **kwargs: Any) -> None: ...

    def _dispatch_zone_change_events(
        self,
        card: CardInstance,
        **kwargs: Any,
    ) -> None: ...

@dataclass(frozen=True, slots=True)
class ZoneDepartureSnapshot:
    origin: str
    controller: str
    logical_object_id: str
    characteristics: Mapping[str, Any]
    attachments: tuple[str, ...]
    attached_to: str | None
    trigger_sources: DepartureTriggerSnapshot
    copy_snapshot: Mapping[str, Any] | None = None
    cast_option: str | None = None
    paid_entry_copy: Mapping[str, Any] | None = None
    multikicker_paid: Mapping[str, Any] | None = None
    declared_cast_costs: Mapping[str, Any] | None = None
    counters: Mapping[str, int] | None = None
    conditional_returns: tuple[ConditionalExileBinding, ...] = ()
    putting_player: str | None = None

    @classmethod
    def from_event_sources(
        cls, *, origin: str, controller: str, logical_object_id: str,
        characteristics: Mapping[str, Any], counters: Mapping[str, int] | None,
        attachments: Sequence[str], attached_to: str | None,
        cast_option: str | None, sources: Sequence[CardInstance],
        source_zones: Mapping[str, str],
        source_characteristics: Mapping[str, Mapping[str, Any]],
        paid_entry_copy: Mapping[str, Any] | None = None,
        multikicker_paid: Mapping[str, Any] | None = None,
        declared_cast_costs: Mapping[str, Any] | None = None,
        putting_player: str | None = None,
    ) -> ZoneDepartureSnapshot:
        """Adapt the historical engine facade's event facts at their typed boundary."""
        return cls(
            origin=origin, controller=controller, logical_object_id=logical_object_id,
            characteristics=characteristics, counters=counters,
            attachments=tuple(attachments), attached_to=attached_to, cast_option=cast_option, paid_entry_copy=paid_entry_copy,
            multikicker_paid=multikicker_paid,
            declared_cast_costs=declared_cast_costs, putting_player=putting_player,
            trigger_sources=DepartureTriggerSnapshot(
                sources=tuple(sources), source_zones=dict(source_zones),
                source_characteristics=dict(source_characteristics),
            ),
        )


@dataclass(frozen=True, slots=True)
class ZoneMovePlan:
    card: CardInstance
    requested_destination: str
    destination: str
    origin: str
    origin_identity_public: bool
    library_position: str | int | None
    destination_type_line: str
    enter_face: str | None
    prepared_replacement: PreparedZoneChange
    prepared_life_payment: PreparedLifePayment | None
    prospective_battle_protector: str | None
    aura_entry_plan: Any
    manifest_entry: bool = False
    reveal_face_down_departure: bool = False


def entry_putting_player(host: ZoneTransitionHost, card: CardInstance, destination: str, player: str | None) -> str | None:
    if player is None or getattr(host.state, "battlefield_entry_player_version", None) is None:
        return None
    if (card.zone == "battlefield" or destination != "battlefield"
            or type(player) is not str or player not in host.active_seats):
        raise GameRuleError("Putting-player authority requires a battlefield entry and an active player")
    return player

def batch_putting_players(host: ZoneTransitionHost, changes, putting_players):
    if putting_players is None or getattr(host.state, "battlefield_entry_player_version", None) is None:
        return {}
    entering = {object_id for object_id, dest in changes if dest == "battlefield"}
    if (not isinstance(putting_players, Mapping) or set(putting_players) - entering
            or any(type(player) is not str for player in putting_players.values())):
        raise GameRuleError("Putting players must be seats keyed by requested battlefield entries")
    return {
        object_id: entry_putting_player(host, host.state.cards[object_id], "battlefield", player)
        for object_id, player in putting_players.items()
    }

def capture_simultaneous_departure(
    host: ZoneTransitionHost,
    card: CardInstance,
    *,
    source_snapshot: DepartureTriggerSnapshot,
    putting_player: str | None = None,
) -> tuple[CardInstance, ZoneDepartureSnapshot]:
    from .attachments import attachment_target_ref
    from .rules.permanent_event_state import current_zone_event_characteristics
    from .conditional_exile_model import source_conditional_bindings
    from .death_history_names import capture_departure_rules_names
    from .creature_power_damage import with_occurrence_power_departure
    return (
        card,
        ZoneDepartureSnapshot(
            origin=card.zone,
            controller=card.controller,
            logical_object_id=card.logical_object_id,
            conditional_returns=source_conditional_bindings(card) if card.zone == 'battlefield' else (),
            characteristics=capture_departure_rules_names(host, card, with_occurrence_power_departure(
                host, card, copy.deepcopy(current_zone_event_characteristics(host, card)),
            )),
            counters=dict(card.counters),
            attachments=tuple(
                host.state.cards[attachment_id].ref
                for attachment_id in card.attachments
                if attachment_id in host.state.cards
            ),
            attached_to=attachment_target_ref(
                host.state.cards,
                host.state.players,
                card,
            ),
            trigger_sources=source_snapshot,
            putting_player=putting_player,
        ),
    )

__all__ = [
    "EXILE_ZONE",
    "HIDDEN_ZONES",
    "JOURNAL_REASON_FIELD",
    "LIBRARY_ZONE",
    "PUBLIC_ZONES",
    "ZoneDepartureSnapshot",
    "ZoneMovePlan",
    "ZoneTransitionHost",
]
