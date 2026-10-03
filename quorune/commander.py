from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .carddb import CardDatabase
from .commander_pairing import validated_commander_counts
from .deck import DeckDefinition
from .model import (
    CONTROL_HISTORY_VERSION,
    CardInstance,
    GameConfig,
    GameState,
    PlayerState,
    TurnHistory,
)


COMMANDER_DAMAGE_IDENTITY_VERSION = 2
_LIBRARY_ZONE = "library"


class CommanderIdentityError(ValueError):
    """A Commander designation or historical identity mode is invalid."""


class CommanderCardView(Protocol):
    object_id: str
    oracle_id: str
    printed_name: str
    owner: str
    is_commander: bool
    commander_designation_id: str | None


class CommanderStateView(Protocol):
    cards: Mapping[str, CommanderCardView]
    commander_damage_identity_version: int | None


@dataclass(frozen=True, slots=True)
class CommanderDamageSource:
    """Public identity for one designated physical commander card."""

    damage_key: str
    designation_id: str | None
    oracle_id: str
    printed_name: str
    owner: str
    legacy_oracle_identity: bool = False


def commander_designation_id(seat: str, ordinal: int) -> str:
    """Return the deterministic game-local identity of a commander card."""

    normalized_seat = str(seat).strip()
    if not normalized_seat or type(ordinal) is not int or ordinal < 1:
        raise CommanderIdentityError(
            "Commander designations require a seat and positive ordinal"
        )
    return f"commander:{normalized_seat}:{ordinal}"


def commander_damage_key(
    *,
    source_is_commander: bool,
    designation_id: str | None,
    oracle_id: str | None,
    identity_version: int | None,
) -> str | None:
    """Select the CR 903.10a ledger key without consulting mutable state.

    New games use the physical card's designation. Historical Game Record v3
    checkpoints that predate the additive identity field retain their former
    Oracle-ID ledger semantics so replay hashes are not silently reinterpreted.
    """

    if not source_is_commander:
        return None
    if identity_version is None:
        legacy = str(oracle_id or "")
        if not legacy:
            raise CommanderIdentityError(
                "Legacy commander damage lost its Oracle identity"
            )
        return legacy
    if identity_version != COMMANDER_DAMAGE_IDENTITY_VERSION:
        raise CommanderIdentityError(
            "Unsupported commander damage identity version "
            f"{identity_version!r}"
        )
    designation = str(designation_id or "")
    if not designation:
        raise CommanderIdentityError(
            "Commander damage lost its physical designation identity"
        )
    return designation


def commander_damage_source(
    state: CommanderStateView,
    damage_key: str,
) -> CommanderDamageSource | None:
    """Resolve one public ledger key without exposing a commander's zone."""

    key = str(damage_key)
    legacy = state.commander_damage_identity_version is None
    for card in state.cards.values():
        if not card.is_commander:
            continue
        candidate = card.oracle_id if legacy else card.commander_designation_id
        if candidate != key:
            continue
        return CommanderDamageSource(
            damage_key=key,
            designation_id=card.commander_designation_id,
            oracle_id=card.oracle_id,
            printed_name=card.printed_name,
            owner=card.owner,
            legacy_oracle_identity=legacy,
        )
    return None


def commander_damage_losers(state: Any) -> tuple[str, ...]:
    """Return active players that lose under the represented CR 903.10a SBA."""

    threshold = int(state.config.commander_damage_to_lose)
    return tuple(
        seat
        for seat in state.active_seats()
        if any(
            int(amount) >= threshold
            for amount in state.players[seat].commander_damage_received.values()
        )
    )


def initial_commander_state(
    card_db: CardDatabase,
    decks: Mapping[str, DeckDefinition],
    *,
    first_player: str | None = None,
    player_names: Mapping[str, str] | None = None,
    config: GameConfig | None = None,
    semantics: Any | None = None,
) -> GameState:
    """Build the authoritative setup state for one Commander game.

    This owns format validation, physical commander designation, initial zone
    population, and deterministic library randomization.  Opening draws and
    mulligan task issuance remain engine commands because they create events.
    """

    config = config or GameConfig()
    if not 2 <= len(decks) <= config.max_players:
        raise ValueError(
            f"CommanderEngine supports 2-{config.max_players} players"
        )
    config.profile = config.effective_profile(len(decks))
    if config.review_profile != "commander_review":
        raise ValueError(
            f"Unsupported review profile {config.review_profile!r}"
        )
    if config.profile not in {"commander_duel", "commander_multiplayer"}:
        raise ValueError(
            f"Unsupported Commander format profile {config.profile!r}"
        )
    if config.trace_level not in {"minimal", "standard", "debug"}:
        raise ValueError(f"Unsupported trace level {config.trace_level!r}")
    if config.semantic_policy not in {
        "arbitrate_or_pause",
        "trusted_only",
    }:
        raise ValueError(
            f"Unsupported semantic policy {config.semantic_policy!r}"
        )

    turn_order = list(decks)
    starting_seat = first_player or turn_order[0]
    if starting_seat not in decks:
        raise ValueError("first_player must name one of the supplied seats")
    while turn_order[0] != starting_seat:
        turn_order.append(turn_order.pop(0))

    names = dict(player_names or {})
    all_seats = list(turn_order)
    players = {
        seat: PlayerState(
            seat=seat,
            name=names.get(seat, seat),
            life=config.starting_life,
        )
        for seat in all_seats
    }
    cards: dict[str, CardInstance] = {}
    commander_ids: dict[str, list[str]] = {seat: [] for seat in all_seats}
    deck_names = {seat: decks[seat].name for seat in all_seats}

    for seat in all_seats:
        deck = decks[seat]
        commander_remaining = validated_commander_counts(
            card_db, semantics, deck
        )
        commander_ordinal = 0
        serial = 0
        for entry in deck.entries:
            if entry.board not in {"mainboard", "commander"}:
                continue
            for _ in range(entry.quantity):
                serial += 1
                record = card_db.lookup(entry.name)
                is_commander = entry.board == "commander"
                if (
                    not is_commander
                    and commander_remaining.get(record.name, 0) > 0
                ):
                    is_commander = True
                if (
                    is_commander
                    and commander_remaining.get(record.name, 0) > 0
                ):
                    commander_remaining[record.name] -= 1
                if is_commander:
                    commander_ordinal += 1
                object_id = uuid.uuid4().hex
                ref = f"{seat}{serial:02d}"
                zone = "command" if is_commander else _LIBRARY_ZONE
                card = CardInstance(
                    object_id=object_id,
                    ref=ref,
                    oracle_id=record.oracle_id,
                    printed_name=record.name,
                    owner=seat,
                    controller=seat,
                    zone=zone,
                    is_commander=is_commander,
                    commander_designation_id=(
                        commander_designation_id(seat, commander_ordinal)
                        if is_commander
                        else None
                    ),
                    known_to=[] if zone == _LIBRARY_ZONE else list(all_seats),
                    revealed_to=(
                        [] if zone == _LIBRARY_ZONE else list(all_seats)
                    ),
                )
                cards[object_id] = card
                players[seat].zones[zone].append(object_id)
                if is_commander:
                    commander_ids[seat].append(record.oracle_id)
        randomizer = random.Random(f"{config.seed}|{seat}|initial")
        randomizer.shuffle(players[seat].zones[_LIBRARY_ZONE])

    return GameState(
        game_id=uuid.uuid4().hex,
        config=config,
        players=players,
        cards=cards,
        deck_names=deck_names,
        commander_oracle_ids=commander_ids,
        turn_order=turn_order,
        current_turn=None,
        last_normal_turn_player=None,
        commander_damage_identity_version=(
            COMMANDER_DAMAGE_IDENTITY_VERSION
        ),
        control_history_version=CONTROL_HISTORY_VERSION,
        turn_history=TurnHistory(departure_history_version=1),
        active_player=None,
        phase="setup",
        step="mulligan",
        ref_counters={},
    )
