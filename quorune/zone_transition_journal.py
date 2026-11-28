from __future__ import annotations

"""Privacy-preserving event journals for committed zone transitions."""

from .errors import GameRuleError
from .model import CardInstance
from .paid_discard_model import record_paid_discard_replacement_reveal
from .replacement.operations import SetField
from .self_graveyard_exile_model import SELF_GRAVEYARD_SHUFFLE_RESULT, RevealForOwnerLibraryShuffle
from .zone_transition_model import (
    HIDDEN_ZONES,
    JOURNAL_REASON_FIELD,
    LIBRARY_ZONE,
    ZoneDepartureSnapshot,
    ZoneMovePlan,
    ZoneTransitionHost,
)


def validate_hidden_search_result_journal(card, destination, *, reveal_to, controller, putting_player, hidden_search_result_viewer):
    if hidden_search_result_viewer is not None and (
        card.zone != "library" or destination != "battlefield" or reveal_to is not None
        or hidden_search_result_viewer != card.owner
        or controller != card.owner or putting_player != card.owner
    ):
        raise GameRuleError("Hidden search journals require a selected card in the searcher's own library")


def prepare_self_graveyard_reveals(host, prepared):
    """Check every selected replacement and original object before any reveal."""
    obligations = []
    for plan in prepared:
        raw = plan.event.payload.get(SELF_GRAVEYARD_SHUFFLE_RESULT)
        if raw is None:
            continue
        try:
            obligation = RevealForOwnerLibraryShuffle.from_dict(raw)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        card = host.state.cards.get(obligation.object_id)
        applied = [effect for effect in plan.effects if effect.effect_id in plan.event.applied_effects
                   and effect.operations == (SetField("destination", "library"), obligation)]
        if (len(applied) != 1 or plan.object_id != obligation.object_id or card is None
                or card.logical_object_id != obligation.logical_object_id or card.zone != plan.origin
                or card.ref != obligation.object_ref or card.owner != obligation.owner
                or card.is_commander is not obligation.is_commander or obligation.owner not in host.active_seats):
            raise GameRuleError("Self-shuffle replacement lost its original moving object or owner")
        obligations.append(obligation)
    return tuple(obligations)


def _self_shuffle_members(host, obligation, physical_components):
    if physical_components is not None and obligation.object_id in physical_components:
        return physical_components[obligation.object_id]
    return (host.state.cards[obligation.object_id],)


def journal_self_graveyard_reveals(host, obligations, *, physical_components=None):
    """Publish the explicitly revealed physical cards after replacement choices commit."""
    for obligation in obligations:
        for card in _self_shuffle_members(host, obligation, physical_components):
            if card.is_card_object:
                record_paid_discard_replacement_reveal(card.logical_object_id)
                host._log(card.owner, "card.reveal", "A card was revealed for its owner-library replacement.",
                          {"player": card.owner, "object": card.ref, "card": card.printed_name},
                          changed_objects=[card.object_id], changed_players=[card.owner])


def prepare_zone_side_effects(host, prepared):
    from .counter_removal import commit_entry_counter_transfer

    reveals = prepare_self_graveyard_reveals(host, prepared)
    commit_entry_counter_transfer(host, prepared)
    journal_self_graveyard_reveals(host, reveals)
    return reveals


def finish_self_graveyard_shuffles(owner, obligations, *, physical_components=None):
    if not obligations:
        return
    shuffles = []
    for obligation in obligations:
        members = _self_shuffle_members(owner.host, obligation, physical_components)
        players = {obligation.owner}
        for card in members:
            if card.is_card_object:
                players.add(card.owner)
                if card.zone == LIBRARY_ZONE:
                    # CR 701.20d gives a revealed library object a fresh incarnation on reorder.
                    owner.reset_zone_change(card, LIBRARY_ZONE, revealed_library_reorder=True)
        shuffles.extend(sorted(players))
    order = {seat: index for index, seat in enumerate(owner.host.apnap_order())}
    for player in sorted(shuffles, key=order.__getitem__):
        library = owner.state.players[player].zones[LIBRARY_ZONE]
        copies = tuple(object_id for object_id in library if not owner.state.cards[object_id].is_card_object)
        owner.shuffle_library(player, reason="self graveyard replacement", excluded_object_ids=copies)


def journal_prevented_token_move(
    host: ZoneTransitionHost, card: CardInstance, origin: str, destination: str, *, log: bool,
) -> None:
    _journal_prevented_zone_move(
        host, card, origin, destination, rule="111.8",
        cause="a token that left the battlefield cannot move again", log=log,
    )


def journal_prevented_nonpermanent_move(
    host: ZoneTransitionHost, card: CardInstance, origin: str, destination: str, *, log: bool,
) -> None:
    _journal_prevented_zone_move(
        host, card, origin, destination, rule="400.4a",
        cause="an instant or sorcery card cannot enter the battlefield", log=log,
    )


def _journal_prevented_zone_move(
    host: ZoneTransitionHost,
    card: CardInstance,
    origin: str,
    destination: str,
    *,
    rule: str,
    cause: str,
    log: bool,
) -> None:
    if not log:
        return
    host._log(
        None,
        "zone.move.prevented",
        f"{card.ref} remained in {origin}; {cause}.",
        {
            "object": card.ref,
            "from": origin,
            "requested_destination": destination,
            "rule": rule,
        },
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[card.owner],
    )


def journal_zone_move(
    host: ZoneTransitionHost,
    plan: ZoneMovePlan,
    departure: ZoneDepartureSnapshot,
    *,
    reveal_to: tuple[str, ...],
    reason: str,
    log: bool,
    unviewed_library_exile: bool = False,
) -> None:
    card = plan.card
    if plan.reveal_face_down_departure:
        host._log(
            card.owner, "card.reveal", f"{card.owner} revealed {card.printed_name} as it returned to hand.",
            {"player": card.owner, "object": card.ref, "card": card.printed_name,
             JOURNAL_REASON_FIELD: "face_down_battlefield_departure"},
            changed_objects=[card.object_id], changed_players=[card.owner],
        )
    if not log:
        return
    from .private_opponent_library_cast_model import (
        has_private_opponent_library_cast_designation, private_opponent_library_exile_viewer,
    )

    private_cast_exile = card.face_down and has_private_opponent_library_cast_designation(card)
    private_cast_viewer = private_opponent_library_exile_viewer(host.state, card) if private_cast_exile else None
    identity_became_hidden = (
        card.zone in HIDDEN_ZONES and not plan.origin_identity_public
    )
    identity_remains_face_down = bool(
        card.face_down
        and card.zone in {"battlefield", "exile", "stack", "command"}
        and (unviewed_library_exile or not plan.origin_identity_public)
    )
    if private_cast_exile or identity_became_hidden or identity_remains_face_down:
        host._log(
            None,
            "zone.move",
            f"{card.owner} moved a card: {departure.origin} → {card.zone}.",
            {
                "from": departure.origin,
                "to": card.zone,
                JOURNAL_REASON_FIELD: reason,
            },
            changed_objects=[card.object_id],
            changed_players=[card.owner, card.controller],
        )
        host._log(
            None,
            "zone.move.private",
            f"{card.ref} {card.printed_name}: {departure.origin} → {card.zone}.",
            {
                "object": card.ref,
                "from": departure.origin,
                "to": card.zone,
                JOURNAL_REASON_FIELD: reason,
                "tapped": card.tapped,
            },
            visibility=(
                ["analyst", *([private_cast_viewer] if private_cast_viewer is not None else [])]
                if private_cast_exile or unviewed_library_exile else sorted({
                    card.owner,
                    card.controller,
                    "analyst",
                    *card.known_to,
                    *reveal_to,
                })
            ),
            changed_objects=[card.object_id],
            changed_players=[card.owner, card.controller],
        )
        return
    host._log(
        None,
        "zone.move",
        f"{card.ref} {card.printed_name}: {departure.origin} → {card.zone}.",
        {
            "object": card.ref,
            "from": departure.origin,
            "to": card.zone,
            JOURNAL_REASON_FIELD: reason,
            "tapped": card.tapped,
        },
        changed_objects=[card.object_id],
        changed_players=[card.owner, card.controller],
    )


__all__ = ["journal_zone_move"]
