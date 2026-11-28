from __future__ import annotations

"""Place the opening land and complete its owner's private exile choice."""

from collections.abc import Mapping
from dataclasses import replace

from ..entry_counter_model import EffectEntryCounter
from ..errors import GameRuleError
from ..opening_hand_model import OpeningHandFrame, OpeningHandSubject
from ..zone_transitions import ZoneTransitionOwner


def put_opening_luck_card_on_battlefield(host, subject: OpeningHandSubject, selections) -> None:
    entry = EffectEntryCounter("luck", 1, subject.player, subject.ref, "103.6a")
    ZoneTransitionOwner(host).move_cards_simultaneously(
        [(subject.object_id, "battlefield")],
        reason="Opening-hand battlefield action with a luck counter",
        destination_controllers={subject.object_id: subject.player},
        putting_players={subject.object_id: subject.player},
        effect_entry_counters={subject.object_id: (entry,)},
        replacement_selections=selections,
    )


def _hand_cards(host, seat):
    return tuple(host.state.cards[object_id] for object_id in host.state.players[seat].zones["hand"])


def issue_opening_luck_exile(host, frame: OpeningHandFrame, source) -> bool:
    seat = frame.seats[frame.index]
    hand = _hand_cards(host, seat)
    if not hand:
        return False
    candidates = [{"id": card.ref, "name": card.printed_name} for card in hand]
    host.permissions.issue(
        kind="opening.hand",
        role="pilot",
        actors=[seat],
        allowed_actions=["choose"],
        payload_by_actor={seat: {
            "prompt": "Choose a card from your hand to exile.",
            "candidates": candidates,
            "legal_actions": [{
                "id": "choose",
                "action": "choose",
                "choice_schema": {"refs": {
                    "type": "object_ref_array", "shape": "ref_array", "label": "Card to exile",
                    "minimum": 1, "maximum": 1, "distinct": True,
                    "legal_refs": [card.ref for card in hand],
                }},
            }],
        }},
        continuation={"opening_luck_exile": {
            "frame": frame.to_dict(),
            "source": [source.object_id, source.logical_object_id],
            "hand": [[card.object_id, card.logical_object_id, card.ref] for card in hand],
        }},
    )
    return True


def complete_opening_luck_exile(host, decision) -> OpeningHandFrame:
    value = decision.continuation["opening_luck_exile"]
    if not isinstance(value, Mapping) or set(value) != {"frame", "source", "hand"}:
        raise GameRuleError("Opening-hand exile continuation is malformed")
    frame = OpeningHandFrame.from_dict(value["frame"])
    frame.validate_state(host)
    if frame.version != 2 or not 0 < frame.index < len(frame.seats):
        raise GameRuleError("Opening-hand exile requires its nonstarting player")
    seat = frame.seats[frame.index]
    if decision.actors != [seat]:
        raise GameRuleError("Opening-hand exile chooser changed")
    source_ref = value["source"]
    if (not isinstance(source_ref, list) or len(source_ref) != 2
            or any(type(item) is not str or not item for item in source_ref)):
        raise GameRuleError("Opening-hand exile source is malformed")
    source = host.state.cards.get(source_ref[0])
    if (source is None or source.logical_object_id != source_ref[1] or source.zone != "battlefield"
            or source.owner != seat or source.controller != seat):
        raise GameRuleError("Opening-hand exile source changed")
    hand = _hand_cards(host, seat)
    if value["hand"] != [[card.object_id, card.logical_object_id, card.ref] for card in hand]:
        raise GameRuleError("The opening hand changed since exile was offered")
    response = decision.responses[seat]
    choices = response.get("choices", {})
    if not isinstance(choices, Mapping):
        raise GameRuleError("Opening-hand exile selection is malformed")
    refs = choices.get("refs", response.get("refs"))
    if type(refs) is not list or len(refs) != 1 or type(refs[0]) is not str:
        raise GameRuleError("Choose exactly one card to exile")
    card = next((card for card in hand if card.ref == refs[0]), None)
    if card is None:
        raise GameRuleError("Choose an offered card from your hand to exile")
    exiled_identity = (card.object_id, card.logical_object_id)
    with host.transaction():
        ZoneTransitionOwner(host).move_card(
            card.object_id, "exile", reason="Opening-hand exile after placing a land with a luck counter",
            semantic_events=True,
        )
    return replace(frame, subjects=tuple(subject for subject in frame.subjects
        if (subject.object_id, subject.logical_object_id) != exiled_identity))
