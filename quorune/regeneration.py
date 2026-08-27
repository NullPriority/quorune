from __future__ import annotations

"""Typed creation of CR 701.19a regeneration replacement effects."""

from typing import Any, Mapping, Protocol, Sequence

from . import damage_results, tap_state


class RegenerationError(ValueError):
    """A regeneration-shield request is malformed or unsupported."""


class RegenerationHost(Protocol):
    state: Any

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str] | None = None,
    ) -> Any: ...

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

    def _remove_object_from_combat(
        self,
        card: Any,
        *,
        reason: str,
    ) -> bool: ...


def create_regeneration_shield(
    host: RegenerationHost,
    object_ref: str,
    *,
    actor: str,
    reason: str,
    logical_object_id: str | None,
) -> str:
    """Create one identity-pinned, until-cleanup regeneration shield."""

    if any(
        type(value) is not str or not value
        for value in (object_ref, actor, reason)
    ) or (
        logical_object_id is not None
        and (type(logical_object_id) is not str or not logical_object_id)
    ):
        raise RegenerationError(
            "Regeneration requires actor, object, optional incarnation, and reason"
        )
    card = next(
        (
            candidate
            for candidate in host.state.cards.values()
            if candidate.ref == object_ref
        ),
        None,
    )
    if card is None:
        card = host._resolve_object(
            actor,
            object_ref,
            zones={"battlefield"},
        )
    expected_logical_object_id = logical_object_id or card.logical_object_id
    if (
        card.zone != "battlefield"
        or bool(card.phased_out)
        or card.logical_object_id != expected_logical_object_id
    ):
        return card.ref
    shields = getattr(card, "regeneration_shields", None)
    if type(shields) is not int or shields < 0:
        raise RegenerationError(
            "Regeneration shield state must be a nonnegative integer"
        )
    card.regeneration_shields = shields + 1
    host._log(
        actor,
        "permanent.regeneration.created",
        f"A regeneration shield was created for {card.ref}.",
        {
            "object": card.ref,
            "shields": card.regeneration_shields,
            "reason": reason,
        },
        importance=1,
        changed_objects=[card.object_id],
        changed_players=[card.controller],
    )
    return card.ref


def apply_regeneration_replacement(
    host: RegenerationHost,
    object_id: str,
    *,
    actor: str | None,
    reason: str,
    logical_object_id: str,
    expected_shields: int,
) -> str:
    """Consume one shield and apply CR 701.19a through canonical owners."""

    if any(
        type(value) is not str or not value
        for value in (object_id, reason, logical_object_id)
    ):
        raise RegenerationError(
            "Regeneration replacement requires object, incarnation, and reason"
        )
    if actor is not None and (type(actor) is not str or not actor):
        raise RegenerationError("Regeneration replacement actor is malformed")
    if type(expected_shields) is not int or expected_shields <= 0:
        raise RegenerationError(
            "Regeneration replacement requires a positive shield snapshot"
        )
    card = host.state.cards.get(object_id)
    if (
        card is None
        or card.zone != "battlefield"
        or bool(card.phased_out)
        or card.logical_object_id != logical_object_id
        or card.regeneration_shields != expected_shields
    ):
        raise RegenerationError("Regeneration replacement state is stale")
    if type(card.tapped) is not bool:
        raise RegenerationError("Regeneration tap state is malformed")
    if type(card.marked_damage) is not int or card.marked_damage < 0:
        raise RegenerationError("Regeneration damage state is malformed")
    if type(card.deathtouch_damage) is not bool:
        raise RegenerationError("Regeneration Deathtouch state is malformed")

    card.regeneration_shields -= 1
    owner_actor = actor or card.controller
    tap_state.set_permanent_tapped(
        host,
        card.ref,
        actor=owner_actor,
        tapped=True,
        reason=reason,
        logical_object_id=logical_object_id,
        log=False,
    )
    damage_results.clear_permanent_damage(
        host,
        card.object_id,
        logical_object_id=logical_object_id,
    )
    host._remove_object_from_combat(
        card,
        reason="regeneration replacement",
    )
    return card.ref


__all__ = [
    "apply_regeneration_replacement",
    "RegenerationError",
    "RegenerationHost",
    "create_regeneration_shield",
]
