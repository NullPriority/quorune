from __future__ import annotations

"""Closed exile/return instruction data; all movement stays zone-owned."""

from dataclasses import dataclass
from typing import Any, Mapping

from .zone_object_keyword_model import ZONE_OBJECT_KEYWORDS


LINKED_EXILE_RETURN_CAPABILITY = "zone.linked_exile_return.fixed"
LINKED_EXILE_RETURN_MECHANIC = "linked-exile-return"
LINKED_EXILE_RETURN_OPERATION = "linked_exile_return"
LINKED_RETURN_OPERATION = "return_linked_exiled_objects"


def validate_linked_instruction(effect: Mapping[str, Any]) -> None:
    """Validate one phase before mutable state is made available."""
    if not isinstance(effect, Mapping):
        raise ValueError("Linked exile/return instruction must be an object")
    phase = effect.get("phase")
    allowed = {"op", "phase", "binding_id", "spec", "_replacement_selections"}
    if phase == "exile":
        allowed.add("cards")
    if effect.get("op") != LINKED_EXILE_RETURN_OPERATION or phase not in {"exile", "return"} or set(effect) - allowed or not {"op", "phase", "binding_id", "spec"}.issubset(effect):
        raise ValueError("Linked exile/return instruction has a closed schema")
    if type(effect["binding_id"]) is not str or not effect["binding_id"].startswith("blink:"):
        raise ValueError("Linked exile/return binding identity is malformed")
    LinkedExileReturnSpec.from_dict(effect["spec"])
    if phase == "exile":
        if "cards" not in effect:
            raise ValueError("Linked exile requires a selected object set")
        cards = effect["cards"]
        if cards is not None and not isinstance(cards, (str, list, tuple)):
            raise ValueError("Linked exile object set is malformed")
    if not isinstance(effect.get("_replacement_selections", ()), (list, tuple)):
        raise ValueError("Linked exile/return replacement selections are malformed")


@dataclass(frozen=True, slots=True)
class LinkedExileReturnSpec:
    timing: str = "immediate"
    controller: str = "owner"
    tapped: bool = False
    entry_counters: tuple[tuple[str, int], ...] = ()
    keywords: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported linked exile/return schema")
        if self.timing not in {"immediate", "next_end_step", "controller_next_end_step"}:
            raise ValueError("Unsupported linked return timing")
        if self.controller not in {"owner", "actor"} or type(self.tapped) is not bool:
            raise ValueError("Linked return controller or tapped state is malformed")
        if len(set(name for name, _ in self.entry_counters)) != len(self.entry_counters):
            raise ValueError("Linked return entry counters must be unique")
        for name, amount in self.entry_counters:
            if name not in {"+1/+1", "flying", "vigilance", "lifelink"} or type(amount) is not int or not 1 <= amount <= 3:
                raise ValueError("Linked return entry counter is outside its fixed scope")
        if len(set(self.keywords)) != len(self.keywords) or any(value.casefold() not in ZONE_OBJECT_KEYWORDS for value in self.keywords):
            raise ValueError("Linked return keywords are unsupported or duplicated")
        if self.keywords and self.timing != "immediate":
            raise ValueError("Delayed keyword-result riders are not represented")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "timing": self.timing,
                "controller": self.controller, "tapped": self.tapped,
                "entry_counters": [[name, amount] for name, amount in self.entry_counters],
                "keywords": list(self.keywords)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LinkedExileReturnSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version", "timing", "controller", "tapped", "entry_counters", "keywords",
        }:
            raise ValueError("Linked exile/return spec has a closed schema")
        counters, keywords = value["entry_counters"], value["keywords"]
        if not isinstance(counters, (list, tuple)) or any(not isinstance(entry, (list, tuple)) or len(entry) != 2 for entry in counters):
            raise ValueError("Linked return entry counters must be pairs")
        if not isinstance(keywords, (list, tuple)) or any(type(keyword) is not str for keyword in keywords):
            raise ValueError("Linked return keywords must be strings")
        return cls(schema_version=value["schema_version"], timing=value["timing"],
                   controller=value["controller"], tapped=value["tapped"],
                   entry_counters=tuple(tuple(entry) for entry in counters), keywords=tuple(keywords))
