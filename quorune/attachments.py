from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, MutableMapping

from .continuous_effect_model import ContinuousObjectIdentity
from .model import CardInstance, PlayerState


PLAYER_ATTACHMENT_PREFIX = "player:"


class AttachmentRelationError(ValueError):
    """An authoritative attachment relation is malformed or cannot commit."""


@dataclass(frozen=True, slots=True)
class AttachmentTransition:
    source_id: str
    previous_target_id: str | None
    target_id: str | None
    changed: bool
    source_timestamp: int


@dataclass(frozen=True, slots=True)
class PendingAttachment:
    target_ref: str
    target_zone: str


def player_attachment_target_id(seat: str) -> str:
    if type(seat) is not str or not seat or seat.startswith(
        PLAYER_ATTACHMENT_PREFIX
    ):
        raise AttachmentRelationError(
            "Player attachment targets require one nonempty seat"
        )
    return f"{PLAYER_ATTACHMENT_PREFIX}{seat}"


def attached_player_seat(source: CardInstance) -> str | None:
    value = source.attached_to
    if not isinstance(value, str) or not value.startswith(
        PLAYER_ATTACHMENT_PREFIX
    ):
        return None
    seat = value.removeprefix(PLAYER_ATTACHMENT_PREFIX)
    return seat or None


def current_attached_player_seat(source: CardInstance, players: Mapping[str, PlayerState]) -> str | None:
    """Read one present reciprocal player attachment without changing it."""
    seat=attached_player_seat(source)
    player=players.get(seat or '')
    if (source.zone!='battlefield' or source.phased_out or player is None or not getattr(player,'in_game',True)
            or source.object_id not in player.attachments):return None
    return seat


def attachment_target_ref(
    cards: Mapping[str, CardInstance],
    players: Mapping[str, PlayerState],
    source: CardInstance,
) -> str | None:
    """Return the public ref of one reciprocal object or player relation."""

    target = cards.get(source.attached_to or "")
    if target is not None and source.object_id in target.attachments:
        return target.ref
    seat = attached_player_seat(source)
    player = players.get(seat or "")
    if player is not None and source.object_id in player.attachments:
        return player.seat
    return None


def _previous_target(
    cards: Mapping[str, CardInstance],
    players: Mapping[str, PlayerState] | None,
    source: CardInstance,
) -> tuple[CardInstance | None, PlayerState | None]:
    previous_id = source.attached_to
    if previous_id is None:
        return None, None
    previous = cards.get(previous_id)
    if previous is not None:
        if source.object_id not in previous.attachments:
            raise AttachmentRelationError(
                "Attachment source and previous target are not reciprocal"
            )
        return previous, None
    seat = attached_player_seat(source)
    player = players.get(seat or "") if players is not None else None
    if player is None or source.object_id not in player.attachments:
        raise AttachmentRelationError(
            "Attachment source names a missing or nonreciprocal previous target"
        )
    return None, player


def _remove_previous(
    source: CardInstance,
    previous: CardInstance | None,
    previous_player: PlayerState | None,
) -> None:
    if previous is not None:
        previous.attachments.remove(source.object_id)
    if previous_player is not None:
        previous_player.attachments.remove(source.object_id)


def take_pending_attachment(source: CardInstance) -> PendingAttachment | None:
    """Consume one represented deferred attachment instruction."""

    target_ref = source.annotations.pop("pending_aura_target", None)
    if not target_ref:
        return None
    return PendingAttachment(
        target_ref=str(target_ref),
        target_zone=str(
            source.annotations.pop("pending_aura_zone", "graveyard")
        ),
    )


def attached_object_identity(
    cards: Mapping[str, CardInstance],
    source: CardInstance,
) -> ContinuousObjectIdentity | None:
    """Return the exact live object affected by an attached-source effect.

    Both halves of the relation are checked.  Stale copied annotations or a
    one-sided object ID therefore cannot grant characteristics.
    """

    target_id = source.attached_to
    if (
        source.zone != "battlefield"
        or source.phased_out
        or not target_id
    ):
        return None
    target = cards.get(target_id)
    if (
        target is None
        or target.zone != "battlefield"
        or target.phased_out
        or source.object_id not in target.attachments
    ):
        return None
    return ContinuousObjectIdentity(
        object_id=target.object_id,
        logical_object_id=target.logical_object_id,
    )


def attach_objects(
    cards: MutableMapping[str, CardInstance],
    source: CardInstance,
    target: CardInstance,
    *,
    source_timestamp: int,
    players: Mapping[str, PlayerState] | None = None,
) -> AttachmentTransition:
    """Commit one CR 701.3 relation and its source timestamp atomically.

    The caller owns game-specific legality.  This function owns reciprocal
    identity integrity and CR 701.3c's new timestamp when the source becomes
    attached to a different object.
    """

    if source.object_id == target.object_id:
        raise AttachmentRelationError("An object cannot attach to itself")
    if cards.get(source.object_id) is not source:
        raise AttachmentRelationError("Attachment source is not authoritative")
    if cards.get(target.object_id) is not target:
        raise AttachmentRelationError("Attachment target is not authoritative")
    if type(source_timestamp) is not int or source_timestamp < 0:
        raise AttachmentRelationError(
            "Attachment timestamps must be nonnegative integers"
        )

    previous_id = source.attached_to
    previous, previous_player = _previous_target(cards, players, source)
    target_mentions_source = source.object_id in target.attachments
    if previous_id == target.object_id:
        if not target_mentions_source:
            raise AttachmentRelationError(
                "Attachment source and target are not reciprocal"
            )
        return AttachmentTransition(
            source_id=source.object_id,
            previous_target_id=previous_id,
            target_id=target.object_id,
            changed=False,
            source_timestamp=source.zone_timestamp,
        )
    if target_mentions_source:
        raise AttachmentRelationError(
            "Attachment target already names an unrelated source relation"
        )

    _remove_previous(source, previous, previous_player)
    source.attached_to = target.object_id
    target.attachments.append(source.object_id)
    source.zone_timestamp = source_timestamp
    return AttachmentTransition(
        source_id=source.object_id,
        previous_target_id=previous_id,
        target_id=target.object_id,
        changed=True,
        source_timestamp=source_timestamp,
    )


def attach_to_player(
    cards: MutableMapping[str, CardInstance],
    players: MutableMapping[str, PlayerState],
    source: CardInstance,
    seat: str,
    *,
    source_timestamp: int,
) -> AttachmentTransition:
    """Commit one reciprocal CR 701.3 relation to a player."""

    if cards.get(source.object_id) is not source:
        raise AttachmentRelationError("Attachment source is not authoritative")
    target = players.get(seat)
    if target is None:
        raise AttachmentRelationError("Attachment player is not authoritative")
    if type(source_timestamp) is not int or source_timestamp < 0:
        raise AttachmentRelationError(
            "Attachment timestamps must be nonnegative integers"
        )
    target_id = player_attachment_target_id(seat)
    previous_id = source.attached_to
    previous, previous_player = _previous_target(cards, players, source)
    target_mentions_source = source.object_id in target.attachments
    if previous_id == target_id:
        if not target_mentions_source:
            raise AttachmentRelationError(
                "Attachment source and player are not reciprocal"
            )
        return AttachmentTransition(
            source_id=source.object_id,
            previous_target_id=previous_id,
            target_id=target_id,
            changed=False,
            source_timestamp=source.zone_timestamp,
        )
    if target_mentions_source:
        raise AttachmentRelationError(
            "Attachment player already names an unrelated source relation"
        )
    _remove_previous(source, previous, previous_player)
    source.attached_to = target_id
    target.attachments.append(source.object_id)
    source.zone_timestamp = source_timestamp
    return AttachmentTransition(
        source_id=source.object_id,
        previous_target_id=previous_id,
        target_id=target_id,
        changed=True,
        source_timestamp=source_timestamp,
    )


def detach_object(
    cards: MutableMapping[str, CardInstance],
    source: CardInstance,
    *,
    players: MutableMapping[str, PlayerState] | None = None,
) -> AttachmentTransition:
    """Remove one reciprocal attachment relation without changing timestamp."""

    if cards.get(source.object_id) is not source:
        raise AttachmentRelationError("Attachment source is not authoritative")
    previous_id = source.attached_to
    if previous_id is None:
        return AttachmentTransition(
            source_id=source.object_id,
            previous_target_id=None,
            target_id=None,
            changed=False,
            source_timestamp=source.zone_timestamp,
        )
    previous, previous_player = _previous_target(cards, players, source)
    _remove_previous(source, previous, previous_player)
    source.attached_to = None
    return AttachmentTransition(
        source_id=source.object_id,
        previous_target_id=previous_id,
        target_id=None,
        changed=True,
        source_timestamp=source.zone_timestamp,
    )


def clear_object_attachment_relations(
    cards: MutableMapping[str, CardInstance],
    card: CardInstance,
    *,
    players: MutableMapping[str, PlayerState] | None = None,
) -> tuple[AttachmentTransition, ...]:
    """Detach an object and everything attached to it before a zone change."""

    transitions: list[AttachmentTransition] = []
    if card.attached_to is not None:
        transitions.append(detach_object(cards, card, players=players))
    for source_id in tuple(card.attachments):
        source = cards.get(source_id)
        if source is None or source.attached_to != card.object_id:
            raise AttachmentRelationError(
                "Attached object and target are not reciprocal"
            )
        transitions.append(detach_object(cards, source, players=players))
    return tuple(transitions)


__all__ = [
    "AttachmentRelationError",
    "AttachmentTransition",
    "PendingAttachment",
    "attach_to_player",
    "attach_objects",
    "attached_player_seat",
    "attached_object_identity",
    "attachment_target_ref",
    "clear_object_attachment_relations",
    "detach_object",
    "player_attachment_target_id",
    "take_pending_attachment",
]
