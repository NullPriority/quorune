from __future__ import annotations

"""Closed values and current-source binding for a linked card-type choice."""

from collections.abc import Mapping
from dataclasses import dataclass

from .rules.linked_entry_opponent import entry_opponent_copy_form

CAPABILITY = "zone.entry.linked_card_type_cost"
ENTRY_HANDLER = "replacement.zone.linked-card-type.v1"
ENTRY_TEMPLATE = "intrinsic-entry-linked-card-type-v1"
COST_HANDLER = "modification.cast-cost.linked-card-type.v1"
COST_TEMPLATE = "public-linked-card-type-cost-v1"
ENTRY_FIELD = "entry_linked_card_type"
ANNOTATION = "linked_entry_card_type"

# CR 205.2a, excluding the two types named by this choice. Some types have
# no matching spells in Commander, but are still legal choices.
OPTIONS = (
    "artifact", "battle", "conspiracy", "dungeon", "enchantment", "instant",
    "kindred", "phenomenon", "plane", "planeswalker", "scheme", "sorcery", "vanguard",
)


@dataclass(frozen=True, slots=True)
class LinkedCardTypeChoice:
    card_type: str
    component_id: str
    origin_logical_id: str

    def __post_init__(self):
        if type(self.card_type) is not str or self.card_type not in OPTIONS:
            raise ValueError("Choose an existing card type other than creature or land")
        if type(self.origin_logical_id) is not str or not self.origin_logical_id:
            raise ValueError("The card type requires the pre-entry object identity")
        if type(self.component_id) is not str or not self.component_id.endswith(":0"):
            raise ValueError("The card type must belong to its original entry component")

    def to_dict(self):
        return {"card_type": self.card_type, "component_id": self.component_id,
                "origin_logical_id": self.origin_logical_id}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"card_type", "component_id", "origin_logical_id"}:
            raise ValueError("The linked card type requires its exact entry choice")
        return cls(value["card_type"], value["component_id"], value["origin_logical_id"])

    def effect_id(self, ref):
        return f"{ENTRY_HANDLER}:{ref}:{self.origin_logical_id}:{self.component_id}:{self.card_type}"


def bound_card_type(source, program_key):
    value = source.annotations.get(ANNOTATION)
    if not isinstance(value, Mapping) or set(value) != {
        "choice", "logical_object_id", "face", "copy_form", "consumer",
    }:
        return None
    if (
        source.zone != "battlefield" or source.phased_out
        or source.logical_object_id != value["logical_object_id"]
        or source.active_face != value["face"]
        or program_key != value["consumer"]
        or entry_opponent_copy_form(source) != value["copy_form"]
    ):
        return None
    return LinkedCardTypeChoice.from_dict(value["choice"]).card_type
