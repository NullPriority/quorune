from __future__ import annotations

"""Private subset selection for a pending Amplify entry replacement."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..rules.amplify import AMPLIFY_REVEALS_FIELD, AmplifyHandCard, AmplifySpec


@dataclass(frozen=True, slots=True)
class AmplifyEntryOperation:
    controller: str
    spec: AmplifySpec
    candidates: tuple[AmplifyHandCard, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (type(self.schema_version) is not int or self.schema_version != 1
            or type(self.controller) is not str or not self.controller or not isinstance(self.spec, AmplifySpec)
            or type(self.candidates) is not tuple or any(
                not isinstance(card, AmplifyHandCard) or card.player != self.controller for card in self.candidates
            ) or any(
                len({getattr(card, key) for card in self.candidates}) != len(self.candidates)
                for key in ("object_id", "logical_object_id", "ref")
            )):
            raise ValueError("Amplify requires distinct captured cards from the entrant controller's hand")

    def to_dict(self) -> dict:
        return {"op": "prepare_amplify_entry", "controller": self.controller,
            "spec": self.spec.to_dict(), "candidates": [card.to_dict() for card in self.candidates]}

    @classmethod
    def from_dict(cls, value: Mapping) -> AmplifyEntryOperation:
        if (not isinstance(value, Mapping) or set(value) != {"op", "controller", "spec", "candidates"}
            or value["op"] != "prepare_amplify_entry" or not isinstance(value["candidates"], (tuple, list))):
            raise ValueError("Amplify entry operation fields are closed")
        return cls(value["controller"], AmplifySpec.from_dict(value["spec"]),
            tuple(AmplifyHandCard.from_dict(card) for card in value["candidates"]))

    def eligible_cards(self, event) -> tuple[AmplifyHandCard, ...]:
        scope = event.entry_scope
        if (event.kind != "zone.change" or event.affected_object is None or scope is None
            or event.affected_object.object_id not in scope.entering_objects
            or event.payload.get("destination") != "battlefield"
            or event.payload.get("destination_controller") != self.controller):
            return ()
        prospective_types = set(event.payload.get("object_types", ()))
        entering = set(scope.entering_objects)
        return tuple(card for card in self.candidates
            if card.object_id not in entering and prospective_types.intersection(card.creature_types))


def apply_amplify_entry_operation(event, payload, children, operation, *, effect_id, objects):
    from .entry_counter_application import add_entry_counters
    from .model import ReplacementEffectError
    from .operations import AddAffectedObjectCounter

    cards = {card.ref: card for card in operation.eligible_cards(event)}
    selected = () if objects is None else objects
    if (not isinstance(selected, (tuple, list)) or any(type(ref) is not str for ref in selected)
        or len(set(selected)) != len(selected) or any(ref not in cards for ref in selected)):
        raise ReplacementEffectError("Amplify requires distinct eligible hand references")
    if not cards:
        raise ReplacementEffectError("Amplify has no eligible cards in this entry occurrence")
    if not selected:
        return
    prior = payload.get(AMPLIFY_REVEALS_FIELD, ())
    payload[AMPLIFY_REVEALS_FIELD] = [*prior, *(cards[ref].reveal() for ref in selected)]
    amount = operation.spec.multiplier * len(selected)
    if amount:
        counter = AddAffectedObjectCounter(counter_name="+1/+1", amount=amount,
            placing_player=operation.controller, source_ref=payload["object_ref"], sequence=len(prior))
        add_entry_counters(event, payload, children, counter, effect_id=effect_id)
