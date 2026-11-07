"""Closed instructions for the public CR 701.60 designation."""

from collections.abc import Mapping
from dataclasses import dataclass

SUSPECT_OPERATION = "suspect_permanent"
SUSPECT_CAPABILITY = "keyword_action.suspect"
SUSPECT_MECHANIC = "suspect"


def suspect_instruction(value):
    if not isinstance(value, Mapping) or value.get("op") != SUSPECT_OPERATION:
        raise ValueError("Suspect requires its typed operation")
    if dict(value) == {"op": SUSPECT_OPERATION, "clear": "suspected_creatures"}:
        return True, None
    if set(value) == {"op", "card"} and (value["card"] is None or type(value["card"]) is str and value["card"]):
        return False, value["card"]
    if set(value) == {"op", "cards"}:
        cards = value["cards"]
        if isinstance(cards, (list, tuple)) and len(cards) <= 1 and all(type(card) is str and card for card in cards):
            return False, cards[0] if cards else None
    raise ValueError("Suspect requires one bound permanent or the fixed creature set")


@dataclass(frozen=True, slots=True)
class SuspectIntent:
    actor: str
    card_ref: str | None
    clear_creatures: bool
