"""Closed source-local battlefield entry after an opponent-caused discard."""

from collections.abc import Mapping
from dataclasses import dataclass

from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD


OPPONENT_DISCARD_ENTRY_HANDLER = "replacement.zone.opponent-discard-entry.v1"
OPPONENT_DISCARD_ENTRY_CAPABILITY = "zone.change.opponent_discard_to_battlefield"
OPPONENT_DISCARD_ENTRY_COUNTER_CAPABILITY = "counter.producer.opponent_discard_entry"
OPPONENT_DISCARD_ENTRY_EVENT = "zone.change"


@dataclass(frozen=True, slots=True)
class OpponentDiscardEntrySpec:
    entry_counters: int = 0

    def __post_init__(self):
        if type(self.entry_counters) is not int or self.entry_counters not in {0, 2}:
            raise ValueError("An opponent-discard entry has either no counters or two +1/+1 counters")

    @property
    def dependencies(self):
        return (
            OPPONENT_DISCARD_ENTRY_CAPABILITY,
            "zone.change.destination_replacement",
            *((OPPONENT_DISCARD_ENTRY_COUNTER_CAPABILITY,) if self.entry_counters else ()),
        )

    def to_descriptor(self):
        return {
            "handler_id": OPPONENT_DISCARD_ENTRY_HANDLER,
            "schema_version": 1,
            "event": OPPONENT_DISCARD_ENTRY_EVENT,
            "entry_counters": self.entry_counters,
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        }

    @classmethod
    def from_descriptor(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {
                "handler_id", "schema_version", "event", "entry_counters", REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
        } or value["handler_id"] != OPPONENT_DISCARD_ENTRY_HANDLER
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["event"] != OPPONENT_DISCARD_ENTRY_EVENT
                or value[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise ValueError("Opponent-discard entry requires its complete versioned replacement descriptor")
        return cls(value["entry_counters"])
