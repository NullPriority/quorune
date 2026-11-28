from __future__ import annotations

"""Closed values and current-source binding for a linked card-type choice."""

from collections.abc import Mapping
from dataclasses import dataclass

from .rules.linked_entry_opponent import entry_opponent_copy_form
from .type_subtype_vocabulary import CARD_TYPES

CAPABILITY = "zone.entry.linked_card_type_cost"
ENTRY_HANDLER = "replacement.zone.linked-card-type.v1"
ENTRY_TEMPLATE = "intrinsic-entry-linked-card-type-v1"
COST_HANDLER = "modification.cast-cost.linked-card-type.v1"
COST_TEMPLATE = "public-linked-card-type-cost-v1"
PROTECTION_CAPABILITY = "continuous.ability.linked_card_type_protection"
PROTECTION_ENTRY_HANDLER = "replacement.zone.linked-protection-card-type.v1"
PROTECTION_ENTRY_TEMPLATE = "intrinsic-entry-linked-protection-card-type-v1"
PROTECTION_HANDLER = "continuous.characteristics.linked_card_type_protection.v1"
PROTECTION_TEMPLATE = "linked-card-type-controller-and-creature-protection-v1"
ENTRY_FIELD = "entry_linked_card_type"
ANNOTATION = "linked_entry_card_type"

# CR 205.2a, excluding the two types named by this choice. Some types have
# no matching spells in Commander, but are still legal choices.
OPTIONS = tuple(card_type for card_type in CARD_TYPES if card_type not in {"creature", "land"})


@dataclass(frozen=True, slots=True)
class LinkedCardTypeChoice:
    card_type: str
    component_id: str
    origin_logical_id: str
    all_types: bool = False

    def __post_init__(self):
        if type(self.all_types) is not bool:
            raise ValueError("A linked card type requires a closed choice range")
        if type(self.card_type) is not str or self.card_type not in (CARD_TYPES if self.all_types else OPTIONS):
            raise ValueError("Choose a card type from the entry effect's legal types")
        if type(self.origin_logical_id) is not str or not self.origin_logical_id:
            raise ValueError("The card type requires the pre-entry object identity")
        if type(self.component_id) is not str or not self.component_id.endswith(":0"):
            raise ValueError("The card type must belong to its original entry component")

    def to_dict(self):
        result = {"card_type": self.card_type, "component_id": self.component_id,
                  "origin_logical_id": self.origin_logical_id}
        if self.all_types:
            result["all_types"] = True
        return result

    @classmethod
    def from_dict(cls, value):
        fields = {"card_type", "component_id", "origin_logical_id"}
        if not isinstance(value, Mapping) or not fields.issubset(value) or set(value) - fields - {"all_types"}:
            raise ValueError("The linked card type requires its exact entry choice")
        if "all_types" in value and value["all_types"] is not True:
            raise ValueError("The unrestricted choice uses its canonical entry result")
        return cls(value["card_type"], value["component_id"], value["origin_logical_id"], value.get("all_types", False))

    @property
    def templates(self):
        if self.all_types:
            return PROTECTION_ENTRY_TEMPLATE, PROTECTION_TEMPLATE
        return ENTRY_TEMPLATE, COST_TEMPLATE

    def effect_id(self, ref):
        handler = PROTECTION_ENTRY_HANDLER if self.all_types else ENTRY_HANDLER
        return f"{handler}:{ref}:{self.origin_logical_id}:{self.component_id}:{self.card_type}"


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


def linked_card_type_protection_runtime_dependencies(program):
    from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from .entry_designations import ENTRY_DESIGNATION_CAPABILITY

    frames = {
        PROTECTION_ENTRY_TEMPLATE: (PROTECTION_ENTRY_HANDLER, "zone.change", "all"),
        PROTECTION_TEMPLATE: (PROTECTION_HANDLER, "characteristics.evaluate", "battlefield"),
    }
    selected = frames.get(program.provenance.get("template_id"))
    if selected is None and not any(
        handler.get("handler_id") in {PROTECTION_HANDLER, PROTECTION_ENTRY_HANDLER}
        for handler in program.handlers
    ):
        return ()
    if selected is None:
        raise ValueError("A linked type Protection handler requires its paired template")
    handler, event, zone = selected
    if (
        (program.event, program.active_zone) != (event, zone)
        or program.effects or program.cost_schema is not None or program.target_schema is not None
        or program.event_condition is not None or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or len(program.handlers) != 1
        or type(program.handlers[0].get("schema_version")) is not int
        or dict(program.handlers[0]) != {"handler_id": handler, "schema_version": 1, "event": event}
    ):
        raise ValueError("A linked type Protection program requires its complete source scope")
    if handler == PROTECTION_ENTRY_HANDLER:
        return ENTRY_DESIGNATION_CAPABILITY, PROTECTION_CAPABILITY
    return (PROTECTION_CAPABILITY,)
