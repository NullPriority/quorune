from __future__ import annotations

"""Typed boundary values for authoritative zone transitions."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .model import CardInstance, GameState
from .life_state import PreparedLifePayment
from .semantic_runtime import PreparedZoneChange
from .zone_trigger_processing import DepartureTriggerSnapshot


EXILE_ZONE = "exile"
LIBRARY_ZONE = "library"
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
    cast_option: str | None = None


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
