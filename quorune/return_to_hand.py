from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .rules.single_object_zone_transition import (
    SingleObjectDestination,
    SingleObjectOrigin,
    SingleObjectZoneTransitionEntry,
    SingleObjectZoneTransitionError,
    SingleObjectZoneTransitionPlan,
    SingleObjectZoneTransitionRequest,
    commit_prevalidated_single_object_zone_transition,
    prepare_single_object_zone_transition,
    request_for_card,
    validate_single_object_zone_transition_plan,
)


_REASON_FIELD = "reason"


ReturnToHandError = SingleObjectZoneTransitionError
ReturnToHandRequest = SingleObjectZoneTransitionRequest
ReturnToHandEntry = SingleObjectZoneTransitionEntry
ReturnToHandPlan = SingleObjectZoneTransitionPlan


class ReturnToHandHost(Protocol):
    state: Any

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str] | None = None,
    ) -> Any: ...

    def move_card(self, object_id: str, destination: str, **kwargs: Any) -> Any: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ReturnToHandResult:
    object_id: str
    object_ref: str
    owner: str
    origin_controller: str
    destination: str
    logical_object_id: str

    def __post_init__(self) -> None:
        if not all(
            type(value) is str and value
            for value in (
                self.object_id,
                self.object_ref,
                self.owner,
                self.origin_controller,
                self.destination,
                self.logical_object_id,
            )
        ):
            raise ReturnToHandError(
                "Return results require complete committed identity"
            )

    @property
    def returned_to_hand(self) -> bool:
        return self.destination == SingleObjectDestination.OWNER_HAND.value


def prepare_return_to_owner_hand(
    host: ReturnToHandHost,
    request: ReturnToHandRequest,
    *,
    actor: str,
    reason: str,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
) -> ReturnToHandPlan:
    return prepare_single_object_zone_transition(
        host,
        request,
        actor=actor,
        reason=reason,
        requested_destination=SingleObjectDestination.OWNER_HAND,
        expected_origin=SingleObjectOrigin.BATTLEFIELD,
        replacement_selections=replacement_selections,
    )


def prepare_graveyard_card_return_to_owner_hand(
    host: ReturnToHandHost,
    request: ReturnToHandRequest,
    *,
    actor: str,
    reason: str,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
) -> ReturnToHandPlan:
    card = host.state.cards.get(request.object_id)
    if card is None or card.owner != actor:
        raise ReturnToHandError(
            "Graveyard return requires a card owned by the acting player"
        )
    if not bool(getattr(card, "is_card_object", True)):
        raise ReturnToHandError(
            "Graveyard return requires a physical card object"
        )
    return prepare_single_object_zone_transition(
        host,
        request,
        actor=actor,
        reason=reason,
        requested_destination=SingleObjectDestination.OWNER_HAND,
        expected_origin=SingleObjectOrigin.GRAVEYARD,
        replacement_selections=replacement_selections,
    )


def validate_return_to_hand_plan(
    host: ReturnToHandHost,
    plan: ReturnToHandPlan,
) -> None:
    if (
        not isinstance(plan, ReturnToHandPlan)
        or plan.requested_destination is not SingleObjectDestination.OWNER_HAND
        or plan.entry.origin is not SingleObjectOrigin.BATTLEFIELD
    ):
        raise ReturnToHandError("Permanent return requires a typed hand plan")
    validate_single_object_zone_transition_plan(host, plan)


def validate_graveyard_card_return_to_hand_plan(
    host: ReturnToHandHost,
    plan: ReturnToHandPlan,
) -> None:
    if (
        not isinstance(plan, ReturnToHandPlan)
        or plan.requested_destination is not SingleObjectDestination.OWNER_HAND
        or plan.entry.origin is not SingleObjectOrigin.GRAVEYARD
        or plan.entry.owner != plan.actor
    ):
        raise ReturnToHandError(
            "Graveyard card return requires a typed own-graveyard hand plan"
        )
    validate_single_object_zone_transition_plan(host, plan)


def _committed_return_result(
    host: ReturnToHandHost,
    plan: ReturnToHandPlan,
    *,
    event_code: str,
    summary: str,
    include_origin: bool = False,
) -> ReturnToHandResult:
    transition = commit_prevalidated_single_object_zone_transition(host, plan)
    result = ReturnToHandResult(
        object_id=transition.object_id,
        object_ref=transition.object_ref,
        owner=transition.owner,
        origin_controller=transition.origin_controller,
        destination=transition.actual_destination,
        logical_object_id=transition.logical_object_id,
    )
    details = {
        "object": result.object_ref,
        "owner": result.owner,
        "origin_controller": result.origin_controller,
        "requested_destination": SingleObjectDestination.OWNER_HAND.value,
        "destination": result.destination,
        _REASON_FIELD: plan.reason,
    }
    if include_origin:
        details["origin"] = plan.entry.origin.value
    host._log(
        plan.actor,
        event_code,
        summary.format(object_ref=result.object_ref),
        details,
        importance=2,
        changed_objects=[result.object_id],
        changed_players=[result.owner, result.origin_controller],
    )
    return result


def commit_return_to_owner_hand(
    host: ReturnToHandHost,
    plan: ReturnToHandPlan,
) -> ReturnToHandResult:
    validate_return_to_hand_plan(host, plan)
    return _committed_return_result(
        host,
        plan,
        event_code="permanent.return_to_owner_hand",
        summary="{object_ref} moved toward its owner's hand.",
    )


def commit_graveyard_card_return_to_owner_hand(
    host: ReturnToHandHost,
    plan: ReturnToHandPlan,
) -> ReturnToHandResult:
    validate_graveyard_card_return_to_hand_plan(host, plan)
    return _committed_return_result(
        host,
        plan,
        event_code="card.return_from_graveyard_to_owner_hand",
        summary="{object_ref} moved from its owner's graveyard toward its hand.",
        include_origin=True,
    )


def return_permanent_to_owner_hand(
    host: ReturnToHandHost,
    object_ref: str,
    *,
    actor: str,
    reason: str,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
) -> ReturnToHandResult:
    card = host._resolve_object(actor, object_ref, zones={"battlefield"})
    return commit_return_to_owner_hand(
        host,
        prepare_return_to_owner_hand(
            host,
            request_for_card(card),
            actor=actor,
            reason=reason,
            replacement_selections=replacement_selections,
        ),
    )


def return_graveyard_card_to_owner_hand(
    host: ReturnToHandHost,
    object_ref: str,
    *,
    actor: str,
    reason: str,
    replacement_selections: Sequence[str | Mapping[str, Any]] = (),
    target_snapshot: Mapping[str, Any] | None = None,
) -> ReturnToHandResult | None:
    if target_snapshot is not None:
        from .selection.target_query import target_identity_matches_snapshot
        if not target_identity_matches_snapshot(host,object_ref,target_snapshot):
            return None
    card = host._resolve_object(actor, object_ref, zones={"graveyard"})
    return commit_graveyard_card_return_to_owner_hand(
        host,
        prepare_graveyard_card_return_to_owner_hand(
            host,
            request_for_card(card),
            actor=actor,
            reason=reason,
            replacement_selections=replacement_selections,
        ),
    )


__all__ = [
    "ReturnToHandEntry",
    "ReturnToHandError",
    "ReturnToHandHost",
    "ReturnToHandPlan",
    "ReturnToHandRequest",
    "ReturnToHandResult",
    "commit_return_to_owner_hand",
    "commit_graveyard_card_return_to_owner_hand",
    "prepare_graveyard_card_return_to_owner_hand",
    "prepare_return_to_owner_hand",
    "request_for_card",
    "return_permanent_to_owner_hand",
    "return_graveyard_card_to_owner_hand",
    "validate_graveyard_card_return_to_hand_plan",
    "validate_return_to_hand_plan",
]
