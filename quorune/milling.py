from __future__ import annotations

"""Typed mandatory fixed-count Mill preparation and commit owner."""

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from .errors import GameRuleError
from .model import CardInstance
from .zone_transition_model import PUBLIC_ZONES
from .zone_transitions import ZoneTransitionOwner


class MillHost(Protocol):
    state: Any

    def _require_seat(self, seat: str, *, in_game: bool = False) -> None: ...

    def _log(self, *args: Any, **kwargs: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class MillRequest:
    actor: str
    player: str
    count: int
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise GameRuleError(
                "Mill requests require an actor, player, and reason"
            )
        if type(self.count) is not int or self.count < 0:
            raise GameRuleError("Mill requests require a nonnegative fixed count")


@dataclass(frozen=True, slots=True)
class MillObjectIdentity:
    object_id: str
    logical_object_id: str
    ref: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.object_id, self.logical_object_id, self.ref)
        ):
            raise GameRuleError("Mill object identities must be complete")

    @classmethod
    def from_card(cls, card: CardInstance) -> "MillObjectIdentity":
        return cls(card.object_id, card.logical_object_id, card.ref)


@dataclass(frozen=True, slots=True)
class MillPlan:
    request: MillRequest
    top_first: tuple[MillObjectIdentity, ...]

    def __post_init__(self) -> None:
        identities = tuple(self.top_first)
        if (
            any(
                not isinstance(value, MillObjectIdentity)
                for value in identities
            )
            or len(identities) > self.request.count
            or len({value.object_id for value in identities}) != len(identities)
        ):
            raise GameRuleError(
                "Mill plans require a bounded unique top snapshot"
            )
        object.__setattr__(self, "top_first", identities)


@dataclass(frozen=True, slots=True)
class MilledCardResult:
    identity: MillObjectIdentity
    destination: str
    destination_logical_object_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, MillObjectIdentity) or any(
            type(value) is not str or not value
            for value in (self.destination, self.destination_logical_object_id)
        ):
            raise GameRuleError("Milled-card results require typed destinations")


@dataclass(frozen=True, slots=True)
class MillResult:
    player: str
    requested_count: int
    cards: tuple[MilledCardResult, ...]

    def __post_init__(self) -> None:
        cards = tuple(self.cards)
        if (
            type(self.requested_count) is not int
            or self.requested_count < 0
            or len(cards) > self.requested_count
            or any(not isinstance(card, MilledCardResult) for card in cards)
        ):
            raise GameRuleError("Mill results require a bounded typed card set")
        object.__setattr__(self, "cards", cards)

    @property
    def actual_count(self) -> int:
        return len(self.cards)

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(card.identity.ref for card in self.cards)


def prepare_mill(host: MillHost, request: MillRequest) -> MillPlan:
    """Snapshot the exact current top cards for one mandatory instruction."""

    host._require_seat(request.actor, in_game=True)
    host._require_seat(request.player, in_game=True)
    library = host.state.players[request.player].zones["library"]
    object_ids = tuple(reversed(library[-request.count :])) if request.count else ()
    return MillPlan(
        request=request,
        top_first=tuple(
            MillObjectIdentity.from_card(host.state.cards[object_id])
            for object_id in object_ids
        ),
    )


def _validate_plan(host: MillHost, plan: MillPlan) -> tuple[str, ...]:
    request = plan.request
    host._require_seat(request.actor, in_game=True)
    host._require_seat(request.player, in_game=True)
    library = host.state.players[request.player].zones["library"]
    object_ids = tuple(identity.object_id for identity in plan.top_first)
    current_top = (
        tuple(reversed(library[-len(object_ids) :])) if object_ids else ()
    )
    if current_top != object_ids:
        raise GameRuleError("The library top changed before Mill committed")
    for identity in plan.top_first:
        card = host.state.cards.get(identity.object_id)
        if (
            card is None
            or card.owner != request.player
            or card.zone != "library"
            or card.logical_object_id != identity.logical_object_id
            or card.ref != identity.ref
        ):
            raise GameRuleError("A prepared Mill object identity changed")
    return object_ids


def commit_mill(host: MillHost, plan: MillPlan) -> MillResult:
    """Commit one validated simultaneous library-to-graveyard instruction."""

    object_ids = _validate_plan(host, plan)
    if plan.request.count == 0:
        return MillResult(player=plan.request.player, requested_count=0, cards=())
    moved: Sequence[CardInstance] = (
        ZoneTransitionOwner(host).move_cards_simultaneously(
            [(object_id, "graveyard") for object_id in object_ids],
            reason=plan.request.reason,
            log=False,
        )
        if object_ids
        else ()
    )
    results = tuple(
        MilledCardResult(
            identity=identity,
            destination=card.zone,
            destination_logical_object_id=card.logical_object_id,
        )
        for identity, card in zip(plan.top_first, moved, strict=True)
    )
    publicly_visible_refs = [
        card.identity.ref
        for card in results
        if card.destination in PUBLIC_ZONES
        and not host.state.cards[card.identity.object_id].face_down
    ]
    host._log(
        plan.request.actor,
        "card.mill",
        f"{plan.request.player} milled {len(results)} card(s).",
        {
            "player": plan.request.player,
            "count": len(results),
            "objects": publicly_visible_refs,
            "reason": plan.request.reason,
        },
        importance=1,
        changed_objects=list(object_ids),
        changed_players=[plan.request.player],
    )
    return MillResult(
        player=plan.request.player,
        requested_count=plan.request.count,
        cards=results,
    )


def mill_cards(host: MillHost, request: MillRequest) -> MillResult:
    return commit_mill(host, prepare_mill(host, request))


__all__ = [
    "commit_mill",
    "MilledCardResult",
    "MillHost",
    "MillObjectIdentity",
    "MillPlan",
    "MillRequest",
    "MillResult",
    "mill_cards",
    "prepare_mill",
]
