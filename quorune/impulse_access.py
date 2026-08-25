from __future__ import annotations

"""Typed fixed-count library exile and temporary play-permission owner."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .errors import GameRuleError
from .impulse_access_model import ImpulseAccessDuration
from .model import CardInstance, GameState
from .zone_transitions import ZoneTransitionOwner


class ImpulseAccessHost(Protocol):
    state: GameState

    def _require_seat(self, seat: str, *, in_game: bool = False) -> None: ...

    def _log(self, *args: Any, **kwargs: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class ImpulseAccessRequest:
    actor: str
    player: str
    count: int
    duration: ImpulseAccessDuration
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise GameRuleError(
                "Impulse-access requests require an actor, player, and reason"
            )
        if type(self.count) is not int or not 1 <= self.count <= 10:
            raise GameRuleError(
                "Impulse-access requests require a fixed count from one to ten"
            )
        if not isinstance(self.duration, ImpulseAccessDuration):
            raise GameRuleError(
                "Impulse-access requests require a typed duration"
            )


@dataclass(frozen=True, slots=True)
class ImpulseAccessObjectIdentity:
    object_id: str
    logical_object_id: str
    ref: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.object_id, self.logical_object_id, self.ref)
        ):
            raise GameRuleError(
                "Impulse-access object identities must be complete"
            )

    @classmethod
    def from_card(cls, card: CardInstance) -> "ImpulseAccessObjectIdentity":
        return cls(card.object_id, card.logical_object_id, card.ref)


@dataclass(frozen=True, slots=True)
class ImpulseAccessPlan:
    request: ImpulseAccessRequest
    top_first: tuple[ImpulseAccessObjectIdentity, ...]

    def __post_init__(self) -> None:
        identities = tuple(self.top_first)
        if (
            not isinstance(self.request, ImpulseAccessRequest)
            or any(
                not isinstance(value, ImpulseAccessObjectIdentity)
                for value in identities
            )
            or len(identities) > self.request.count
            or len({value.object_id for value in identities}) != len(identities)
        ):
            raise GameRuleError(
                "Impulse-access plans require a bounded unique top snapshot"
            )
        object.__setattr__(self, "top_first", identities)


@dataclass(frozen=True, slots=True)
class ImpulseAccessResult:
    player: str
    requested_count: int
    exiled_refs: tuple[str, ...]
    moved_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        exiled = tuple(self.exiled_refs)
        moved = tuple(self.moved_refs)
        if (
            type(self.player) is not str
            or not self.player
            or type(self.requested_count) is not int
            or not 1 <= self.requested_count <= 10
            or len(moved) > self.requested_count
            or len(exiled) > len(moved)
            or any(type(value) is not str or not value for value in moved)
            or any(type(value) is not str or not value for value in exiled)
            or not set(exiled).issubset(moved)
        ):
            raise GameRuleError(
                "Impulse-access results require bounded typed card sets"
            )
        object.__setattr__(self, "exiled_refs", exiled)
        object.__setattr__(self, "moved_refs", moved)


def prepare_fixed_impulse_access(
    host: ImpulseAccessHost,
    request: ImpulseAccessRequest,
) -> ImpulseAccessPlan:
    """Snapshot the exact current top cards for one mandatory instruction."""

    host._require_seat(request.actor, in_game=True)
    host._require_seat(request.player, in_game=True)
    library = host.state.players[request.player].zones["library"]
    object_ids = tuple(reversed(library[-request.count :]))
    return ImpulseAccessPlan(
        request=request,
        top_first=tuple(
            ImpulseAccessObjectIdentity.from_card(
                host.state.cards[object_id]
            )
            for object_id in object_ids
        ),
    )


def _validate_plan(
    host: ImpulseAccessHost,
    plan: ImpulseAccessPlan,
) -> tuple[str, ...]:
    request = plan.request
    host._require_seat(request.actor, in_game=True)
    host._require_seat(request.player, in_game=True)
    object_ids = tuple(identity.object_id for identity in plan.top_first)
    library = host.state.players[request.player].zones["library"]
    current_top = (
        tuple(reversed(library[-len(object_ids) :])) if object_ids else ()
    )
    if current_top != object_ids:
        raise GameRuleError(
            "The library top changed before impulse access committed"
        )
    for identity in plan.top_first:
        card = host.state.cards.get(identity.object_id)
        if (
            card is None
            or card.owner != request.player
            or card.zone != "library"
            or card.logical_object_id != identity.logical_object_id
            or card.ref != identity.ref
        ):
            raise GameRuleError(
                "A prepared impulse-access object identity changed"
            )
    return object_ids


def _play_permission(
    host: ImpulseAccessHost,
    request: ImpulseAccessRequest,
) -> dict[str, Any]:
    permission: dict[str, Any] = {
        "player": request.player,
        "zone": "exile",
        "without_mana_cost": False,
        "allow_land": True,
        "allow_spell": True,
        "duration": request.duration.value,
        "granted_turn_sequence": host.state.turn_sequence,
    }
    if request.duration is ImpulseAccessDuration.END_OF_TURN:
        permission["turn_sequence"] = host.state.turn_sequence
    else:
        permission["expires_at_turns_begun"] = (
            host.state.players[request.player].turns_begun + 1
        )
    return permission


def commit_fixed_impulse_access(
    host: ImpulseAccessHost,
    plan: ImpulseAccessPlan,
) -> ImpulseAccessResult:
    """Move one validated top set and grant only actual exiled objects access."""

    object_ids = _validate_plan(host, plan)
    moved: Sequence[CardInstance] = (
        ZoneTransitionOwner(host).move_cards_simultaneously(
            [(object_id, "exile") for object_id in object_ids],
            reason=plan.request.reason,
            log=False,
        )
        if object_ids
        else ()
    )
    permission = _play_permission(host, plan.request)
    exiled: list[CardInstance] = []
    for card in moved:
        if card.zone != "exile":
            continue
        card.annotations["temporary_play_permission"] = dict(permission)
        exiled.append(card)
    host._log(
        plan.request.actor,
        "library.impulse_access",
        (
            f"{plan.request.player} exiled {len(exiled)} card(s) "
            "with temporary play permission."
        ),
        {
            "player": plan.request.player,
            "count": len(exiled),
            "objects": [card.ref for card in exiled],
            "duration": plan.request.duration.value,
            "reason": plan.request.reason,
        },
        importance=2,
        changed_objects=[card.object_id for card in moved],
        changed_players=[plan.request.player],
    )
    return ImpulseAccessResult(
        player=plan.request.player,
        requested_count=plan.request.count,
        exiled_refs=tuple(card.ref for card in exiled),
        moved_refs=tuple(card.ref for card in moved),
    )


def resolve_fixed_impulse_access(
    host: ImpulseAccessHost,
    request: ImpulseAccessRequest,
) -> ImpulseAccessResult:
    return commit_fixed_impulse_access(
        host,
        prepare_fixed_impulse_access(host, request),
    )


def temporary_play_permission_is_current(
    state: GameState,
    seat: str,
    card: CardInstance,
    permission: Mapping[str, Any],
) -> bool:
    """Validate one zone-pinned temporary permission at its duration boundary."""

    if (
        str(permission.get("player") or "") != seat
        or str(permission.get("zone") or "") != card.zone
    ):
        return False
    duration = permission.get("duration")
    if duration in {None, ImpulseAccessDuration.END_OF_TURN.value}:
        turn_sequence = permission.get("turn_sequence")
        return (
            type(turn_sequence) is int
            and turn_sequence == state.turn_sequence
        )
    if duration != ImpulseAccessDuration.END_OF_NEXT_TURN.value:
        return False
    expires = permission.get("expires_at_turns_begun")
    return (
        type(expires) is int
        and expires >= 0
        and state.players[seat].turns_begun <= expires
    )


def expire_temporary_play_permissions(
    state: GameState,
    *,
    active_player: str | None,
) -> tuple[str, ...]:
    """Expire current-turn and active player's next-turn grants at cleanup."""

    changed: list[str] = []
    for card in state.cards.values():
        permission = card.annotations.get("temporary_play_permission")
        if not isinstance(permission, Mapping):
            continue
        player = str(permission.get("player") or "")
        duration = permission.get("duration")
        remove = False
        if duration in {None, ImpulseAccessDuration.END_OF_TURN.value}:
            turn_sequence = permission.get("turn_sequence")
            remove = (
                type(turn_sequence) is int
                and turn_sequence <= state.turn_sequence
            )
        elif (
            duration == ImpulseAccessDuration.END_OF_NEXT_TURN.value
            and player == active_player
            and player in state.players
        ):
            expires = permission.get("expires_at_turns_begun")
            remove = (
                type(expires) is int
                and state.players[player].turns_begun >= expires
            )
        if remove:
            card.annotations.pop("temporary_play_permission", None)
            changed.append(card.object_id)
    return tuple(changed)


__all__ = [
    "commit_fixed_impulse_access",
    "expire_temporary_play_permissions",
    "ImpulseAccessDuration",
    "ImpulseAccessHost",
    "ImpulseAccessObjectIdentity",
    "ImpulseAccessPlan",
    "ImpulseAccessRequest",
    "ImpulseAccessResult",
    "prepare_fixed_impulse_access",
    "resolve_fixed_impulse_access",
    "temporary_play_permission_is_current",
]
