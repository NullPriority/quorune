"""Sealed identity of the two selected payments in a paused cast."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ...replacement.model import ReplaceableEvent, ReplacementEffectError
from ...replacement.ordering import ReplacementChoiceRequired


ORDERED_ZONE_COST_FRAME_FIELD = "_ordered_zone_cost_frame"
_CHOICE_FIELDS = {"hand": "discard_cards", "battlefield": "sacrifice_cards"}


@dataclass(frozen=True, slots=True)
class OrderedZoneCostFrame:
    spell_ref: str
    option_id: str
    payments: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.spell_ref, self.option_id)):
            raise ReplacementEffectError("The ordered cost frame needs its exact cast and option")
        if (type(self.payments) is not tuple or len(self.payments) != 2
                or any(type(payment) is not tuple or len(payment) != 2
                       or any(type(part) is not str or not part for part in payment)
                       for payment in self.payments)):
            raise ReplacementEffectError("The ordered cost frame requires two selected payments")
        if ({origin for origin, _ in self.payments} != set(_CHOICE_FIELDS)
                or len({ref for _, ref in self.payments}) != 2):
            raise ReplacementEffectError("The ordered cost frame requires distinct cast resources")

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "spell_ref", "option_id", "payments"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not isinstance(value["payments"], (list, tuple))
                or any(not isinstance(payment, (list, tuple)) for payment in value["payments"])):
            raise ReplacementEffectError("The ordered cost frame is malformed")
        return cls(value["spell_ref"], value["option_id"], tuple(tuple(row) for row in value["payments"]))

    def to_dict(self):
        return {"schema_version": 1, "spell_ref": self.spell_ref, "option_id": self.option_id,
                "payments": [list(payment) for payment in self.payments]}

    def validate_response(self, response):
        if response.get("card") != self.spell_ref or response.get("cost_option") not in (None, self.option_id):
            raise ReplacementEffectError("The continuation cast or cost option changed")
        for origin, ref in self.payments:
            selected = response.get(_CHOICE_FIELDS[origin])
            if not isinstance(selected, (list, tuple)) or tuple(selected) != (ref,):
                raise ReplacementEffectError("The continuation selected a different cost object")

    def through_pending_payment(self, event: ReplaceableEvent):
        entry = (event.payload.get("origin"), event.payload.get("object_ref"))
        if event.kind != "zone.change" or event.payload.get("destination") != "graveyard" or entry not in self.payments:
            raise ReplacementEffectError("The pending replacement does not belong to the selected costs")
        return tuple(ref for _, ref in self.payments[:self.payments.index(entry) + 1])


class OrderedZoneCostChoiceRequired(ReplacementChoiceRequired):
    def __init__(self, required: ReplacementChoiceRequired, frame: OrderedZoneCostFrame):
        super().__init__(batch=required.batch, effects=required.effects, pending=required.pending)
        self.frame = frame
