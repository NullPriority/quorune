from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Protocol

from ..mana_undo import priority_actions_with_mana_undo
from ..station import (
    StationAbilityError,
    station_candidates,
    station_cost_choice,
)
from ..util import unique_preserving_order
from .action_proposals import (
    ActionOffer,
    action_offer_signature_facts,
    freeze_json,
)
from .activation.proposal import build_activation_offer
from .activation_costs import activation_choice_candidates
from .casting.proposal import build_cast_offer
from ..morph import MORPH_CAST_METHOD
from .morph_actions import build_turn_face_up_offer


class ActionCatalogHost(Protocol):
    state: Any
    active_seats: list[str]

    def _compiled_zone_cast_permission(self, seat: str, card: Any) -> bool: ...

    def _compiled_land_play_permission(self, seat: str, card: Any) -> bool: ...

    def card_record(self, card: Any) -> Any: ...

    def _land_play_faces(self, record: Any) -> list[Mapping[str, Any] | None]: ...

    def _land_entry_life_amount(
        self, record: Any, face: Mapping[str, Any] | None
    ) -> int: ...

    def _activated_abilities(self, card: Any) -> tuple[Any, ...]: ...

    def _crew_threshold(self, ability: Any) -> int | None: ...

    def _crew_candidates(self, seat: str, source: Any) -> list[Any]: ...

def _cast_candidates(host: ActionCatalogHost, seat: str) -> list[Any]:
    player = host.state.players[seat]
    object_ids = unique_preserving_order(
        [
            *player.zones["hand"],
            *player.zones["command"],
            *[
                object_id
                for zone in ("graveyard", "exile")
                for owner in host.active_seats
                for object_id in host.state.players[owner].zones[zone]
                if host._compiled_zone_cast_permission(
                    seat, host.state.cards[object_id]
                )
            ],
        ]
    )
    return [host.state.cards[object_id] for object_id in object_ids]


def _cast_offers(
    host: ActionCatalogHost, seat: str
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    castable: list[str] = []
    offers: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for card in _cast_candidates(host, seat):
        for cast_method in (None, MORPH_CAST_METHOD):
            result = build_cast_offer(
                host,
                seat,
                card,
                cast_method=cast_method,
            )
            if result.status == "payable":
                assert result.offer is not None
                if card.ref not in castable:
                    castable.append(card.ref)
                offers.append(result.offer.to_dict())
                continue
            if result.reason in {
                "land_not_spell",
                "timing",
                "custom_token",
                "morph_contract_unavailable",
            }:
                continue
            diagnostics.append(
                {
                    "id": (
                        f"cast-morph:{card.ref}"
                        if cast_method == MORPH_CAST_METHOD
                        else f"cast:{card.ref}"
                    ),
                    "kind": "cast",
                    "card": card.ref,
                    "from": card.zone,
                    "status": result.status,
                    "reason": result.reason,
                }
            )
    return castable, offers, diagnostics


def _land_candidates(
    host: ActionCatalogHost, seat: str
) -> list[tuple[Any, Mapping[str, Any] | None]]:
    player = host.state.players[seat]
    if not (
        seat == host.state.active_player
        and not host.state.stack
        and host.state.phase in {"precombat_main", "postcombat_main"}
        and player.land_plays_remaining
    ):
        return []
    result = []
    for object_id in [
        *player.zones["hand"],
        *[
            object_id
            for zone in ("graveyard", "exile")
            for owner in host.active_seats
            for object_id in host.state.players[owner].zones[zone]
        ],
    ]:
        card = host.state.cards[object_id]
        record = host.card_record(card)
        faces = host._land_play_faces(record) if record else []
        if not faces or not host._compiled_land_play_permission(seat, card):
            continue
        result.extend((card, face) for face in faces)
    return result


def _land_offers(
    host: ActionCatalogHost, seat: str
) -> tuple[list[str], list[dict[str, Any]]]:
    candidates = _land_candidates(host, seat)
    lands = unique_preserving_order([card.ref for card, _face in candidates])
    face_counts = {
        ref: sum(1 for card, _face in candidates if card.ref == ref)
        for ref in lands
    }
    offers = []
    for card, face in candidates:
        record = host.card_record(card)
        face_name = str(face.get("name") or "") if face else ""
        display_name = face_name or (record.name if record else card.printed_name)
        action_id = f"play-land:{card.ref}"
        if face_counts[card.ref] > 1:
            face_index = next(
                index
                for index, candidate in enumerate(record.faces)
                if str(candidate.get("name") or "") == face_name
            )
            action_id += f":face-{face_index}"
        payload: dict[str, Any] = {
            "kind": "play_land",
            "card": card.ref,
            "from": card.zone,
        }
        if face_name:
            payload["face"] = face_name
        life_amount = host._land_entry_life_amount(record, face) if record else 0
        if life_amount:
            payload["choice_schema"] = {
                "pay_life": {
                    "type": "boolean",
                    "label": f"Pay {life_amount} life to enter untapped",
                    "default": False,
                    "life": life_amount,
                    "effect": "enters untapped",
                }
            }
        offers.append(
            ActionOffer(
                action_id=action_id,
                action="play_land",
                seat=seat,
                label=f"Play {display_name}",
                expiry_revision=host.state.revision,
                payload=freeze_json(payload),
            ).to_dict()
        )
    return lands, offers


def _ability_hint(
    host: ActionCatalogHost, seat: str, card: Any, ability: Any
) -> dict[str, Any]:
    hint = ability.compact(source_ref=card.ref, zone=card.zone)
    if ability.choices:
        hint["choose_cost"] = [
            {
                **choice.compact(),
                "legal_refs": list(
                    activation_choice_candidates(host, seat, card, choice)
                ),
            }
            for choice in ability.choices
        ]
    threshold = host._crew_threshold(ability)
    if threshold is not None:
        hint["choose_cost"] = [
            {
                "k": "crew",
                "z": "battlefield",
                "minimum": 0 if threshold == 0 else 1,
                "minimum_total_power": threshold,
                "legal_refs": [
                    candidate.ref
                    for candidate in host._crew_candidates(seat, card)
                ],
            }
        ]
    if station_cost_choice(ability) is not None:
        try:
            candidates = station_candidates(host, seat, card)
        except StationAbilityError:
            candidates = ()
        hint["choose_cost"] = [
            {
                "k": "station",
                "z": "battlefield",
                "minimum": 1,
                "maximum": 1,
                "legal_refs": [
                    candidate.ref
                    for candidate in candidates
                ],
            }
        ]
    fetch_types = ability.library_search_types
    if fetch_types:
        hint["search_types"] = list(fetch_types)
    return hint


def _ability_offers(
    host: ActionCatalogHost, seat: str
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    strategic: list[dict[str, Any]] = []
    mana: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    player = host.state.players[seat]
    for zone in ("battlefield", "hand", "graveyard", "exile"):
        for object_id in player.zones[zone]:
            card = host.state.cards[object_id]
            if zone == "battlefield":
                if card.controller != seat or card.phased_out:
                    continue
            elif card.owner != seat:
                continue
            for ability in host._activated_abilities(card):
                if zone not in ability.zones:
                    continue
                hint = _ability_hint(host, seat, card, ability)
                result = build_activation_offer(host, seat, card, ability)
                if result.status != "payable":
                    if result.status in {"unpayable", "unresolved"} or result.reason == "mandatory_target_unavailable":
                        diagnostics.append(
                            {
                                **hint,
                                "id": f"activate:{card.ref}:{ability.ability_id}",
                                "status": result.status,
                                "reason": result.reason,
                            }
                        )
                    continue
                assert result.offer is not None
                if ability.mana_ability:
                    mana.append(hint)
                ordinary_mana = ability.mana_ability and not (
                    ability.choices
                    or ability.life_payment
                    or ability.energy_payment
                    or ability.discard_source
                    or ability.sacrifice_source
                    or ability.exile_source
                    or ability.uncompiled_costs
                )
                if not ordinary_mana:
                    strategic.append(hint)
                offers.append(result.offer.to_dict())
    return strategic, mana, offers, diagnostics


def _concede_offer(host: ActionCatalogHost, seat: str) -> dict[str, Any]:
    return ActionOffer(
        action_id="concede",
        action="concede",
        seat=seat,
        label="Concede game",
        expiry_revision=host.state.revision,
        payload=freeze_json(
            {
                "kind": "concede",
                "choice_schema": {
                    "confirm_concede": {
                        "type": "boolean",
                        "label": "Concede and leave the remaining players in the game",
                        "legal_values": [True],
                        "default": True,
                        "required": True,
                    }
                },
            }
        ),
    ).to_dict()


def _turn_face_up_offers(
    host: ActionCatalogHost,
    seat: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    refs: list[str] = []
    offers: list[dict[str, Any]] = []
    for object_id in host.state.players[seat].zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.controller != seat:
            continue
        offer = build_turn_face_up_offer(host, seat, card)
        if offer is None:
            continue
        refs.append(card.ref)
        offers.append(offer.to_dict())
    return refs, offers


def build_priority_action_catalog(
    host: ActionCatalogHost, seat: str
) -> dict[str, Any]:
    """Project executable offers without reimplementing action legality."""

    castable, cast_offers, cast_diagnostics = _cast_offers(host, seat)
    lands, land_offers = _land_offers(host, seat)
    abilities, mana_abilities, activation_offers, activation_diagnostics = (
        _ability_offers(host, seat)
    )
    turn_face_up, turn_face_up_offers = _turn_face_up_offers(host, seat)
    actions = priority_actions_with_mana_undo(host.state, seat)
    actions.extend(land_offers)
    actions.extend(cast_offers)
    actions.extend(activation_offers)
    actions.extend(turn_face_up_offers)
    actions.append(_concede_offer(host, seat))
    return {
        "cast": castable,
        "lands": lands,
        "abilities": abilities,
        "mana_abilities": mana_abilities,
        "special_actions": turn_face_up,
        "actions": actions,
        "diagnostic": {
            "unpayable": [
                row
                for row in [*cast_diagnostics, *activation_diagnostics]
                if row["status"] != "unresolved"
            ],
            "unresolved_cost_semantics": [
                row
                for row in [*cast_diagnostics, *activation_diagnostics]
                if row["status"] == "unresolved"
            ],
        },
    }


__all__ = [
    "ActionCatalogHost",
    "action_offer_signature_facts",
    "build_priority_action_catalog",
]
