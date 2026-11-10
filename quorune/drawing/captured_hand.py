from __future__ import annotations

"""Exact draw-result identities retained within one resolving instruction."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.library_selection import LibrarySelectionObjectIdentity


CAPTURED_DRAW_CAST_FIELD = "captured_draw_cast"
CAPTURED_DRAW_CAST_CAPABILITY = "zone.draw.revealed_result_cast"
CAPTURED_DRAW_CAST_DEPENDENCIES = (CAPTURED_DRAW_CAST_CAPABILITY, "zone.draw.library_to_hand")
CAPTURED_DRAW_CAST_COUNTS = frozenset({2, 3, 4})
CAPTURED_HAND_CONTEXT = "captured_drawn_hand"
DRAW_CAPTURE_FINISHED = "_draw_capture_finished"
DRAW_CAST_OFFER_FIELDS = frozenset({"op", "player", CAPTURED_DRAW_CAST_FIELD, "_captured"})
DRAW_CAST_INSTRUCTION_FIELDS = DRAW_CAST_OFFER_FIELDS - {"_captured"}


def _require_count(count: int) -> None:
    if type(count) is not int or count not in CAPTURED_DRAW_CAST_COUNTS:
        raise ValueError("A draw-and-cast instruction requires a supported fixed draw count")


def _require_string(value: str) -> None:
    if type(value) is not str or not value:
        raise ValueError("The draw-and-cast instruction requires a current controller and stack reference")


@dataclass(frozen=True, slots=True)
class CaptureDrawnHandCard:
    stack_ref: str
    count: int

    def __post_init__(self) -> None:
        _require_string(self.stack_ref)
        _require_count(self.count)

    def to_dict(self) -> dict:
        return {"action": "capture_drawn_hand_card", "stack_ref": self.stack_ref, "count": self.count}


@dataclass(frozen=True, slots=True)
class CapturedDrawnHand:
    count: int
    cards: tuple[LibrarySelectionObjectIdentity, ...] = ()
    revealed: bool = False

    def __post_init__(self) -> None:
        _require_count(self.count)
        if (type(self.revealed) is not bool or not isinstance(self.cards, tuple)
                or len(self.cards) > self.count
                or any(not isinstance(card, LibrarySelectionObjectIdentity) for card in self.cards)
                or len({card.object_id for card in self.cards}) != len(self.cards)):
            raise ValueError("Captured draws require distinct hand identities within the fixed count")

    @classmethod
    def from_dict(cls, value: Mapping) -> CapturedDrawnHand:
        if (not isinstance(value, Mapping) or set(value) != {"count", "cards", "revealed"}
                or not isinstance(value["cards"], (list, tuple))):
            raise ValueError("The captured draw frame is malformed")
        cards = tuple(LibrarySelectionObjectIdentity.from_dict(card) for card in value["cards"])
        return cls(value["count"], cards, value["revealed"])

    def to_dict(self) -> dict:
        return {"count": self.count, "cards": [card.to_dict() for card in self.cards], "revealed": self.revealed}


def captured_draw_is_public(state, card):
    if card.zone != "hand":
        return False
    for item in state.stack:
        raw = item.context.get(CAPTURED_HAND_CONTEXT)
        if raw is not None and item.controller == card.owner and item.context.get("currently_resolving"):
            hand = CapturedDrawnHand.from_dict(raw)
            if hand.revealed and LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref) in hand.cards:
                return True
    return False


def captured_draw_cast_instruction(effect: Mapping, *, actor: str = "$controller") -> int:
    if (not isinstance(effect, Mapping) or set(effect) != DRAW_CAST_INSTRUCTION_FIELDS
            or effect["op"] != "choose_objects" or effect["player"] != actor):
        raise ValueError("Draw and cast requires one closed controller instruction")
    payload = effect[CAPTURED_DRAW_CAST_FIELD]
    if (not isinstance(payload, Mapping) or set(payload) != {"schema_version", "count"}
            or type(payload["schema_version"]) is not int or payload["schema_version"] != 1):
        raise ValueError("Draw and cast requires its closed fixed-count payload")
    _require_count(payload["count"])
    return payload["count"]


def captured_draw_cast_offer(effect: Mapping, *, actor: str) -> CapturedDrawnHand:
    if set(effect) != DRAW_CAST_OFFER_FIELDS:
        raise ValueError("The resolving draw offer requires its authoritative hand frame")
    count = captured_draw_cast_instruction({key: effect[key] for key in DRAW_CAST_INSTRUCTION_FIELDS}, actor=actor)
    hand = CapturedDrawnHand.from_dict(effect["_captured"])
    if hand.count != count or hand.revealed:
        raise ValueError("The resolving draw offer changed its captured hand")
    return hand


def captured_draw_cast_payload_dependencies(effect: Mapping) -> tuple[str, ...]:
    if CAPTURED_DRAW_CAST_FIELD not in effect:
        return ()
    captured_draw_cast_instruction(effect)
    return CAPTURED_DRAW_CAST_DEPENDENCIES


def captured_draw_cast_node_capabilities(*, effects, target_schema) -> tuple[str, ...]:
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        return captured_draw_cast_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def is_closed_captured_draw_cast_program(program) -> bool:
    required = captured_draw_cast_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    return bool(required) and set(required) <= set(program.capability_dependencies)


@dataclass(frozen=True, slots=True)
class RevealCapturedDrawsIntent:
    actor: str
    stack_ref: str
    hand: CapturedDrawnHand
    keep_revealed: bool

    def __post_init__(self) -> None:
        _require_string(self.actor)
        _require_string(self.stack_ref)
        if not isinstance(self.hand, CapturedDrawnHand) or self.hand.revealed or type(self.keep_revealed) is not bool:
            raise ValueError("Reveal requires the current unrevealed draw frame")


@dataclass(frozen=True, slots=True)
class ResolveCapturedDrawCastIntent:
    actor: str
    stack_ref: str
    hand: CapturedDrawnHand
    options_fingerprint: str
    response: FrozenMap | None
    replacement_selections: tuple = ()

    def __post_init__(self) -> None:
        _require_string(self.actor)
        _require_string(self.stack_ref)
        if (not isinstance(self.hand, CapturedDrawnHand) or self.hand.revealed
                or type(self.options_fingerprint) is not str or len(self.options_fingerprint) != 64
                or self.response is not None and not isinstance(self.response, FrozenMap)
                or not isinstance(self.replacement_selections, tuple)):
            raise ValueError("Cast requires its exact revealed hand, options, and response")

    def identity(self) -> dict:
        return {"actor": self.actor, "stack_ref": self.stack_ref, "hand": self.hand.to_dict(),
                "options_fingerprint": self.options_fingerprint, "response": thaw_value(self.response)}


def validate_captured_draw_cast_identity(value: Mapping) -> dict:
    if not isinstance(value, Mapping) or set(value) != {"actor", "stack_ref", "hand", "options_fingerprint", "response"}:
        raise ValueError("The resolving drawn-card cast identity is malformed")
    raw_response = value["response"]
    if raw_response is not None and not isinstance(raw_response, Mapping):
        raise ValueError("The resolving drawn-card cast response is malformed")
    return ResolveCapturedDrawCastIntent(value["actor"], value["stack_ref"], CapturedDrawnHand.from_dict(value["hand"]),
        value["options_fingerprint"], FrozenMap(raw_response) if raw_response is not None else None).identity()
