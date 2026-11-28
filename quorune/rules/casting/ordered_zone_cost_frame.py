"""Sealed selected zone payments in a paused cast."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ...replacement.model import ReplaceableEvent, ReplacementEffectError
from ...replacement.ordering import ReplacementChoiceRequired


ORDERED_ZONE_COST_FRAME_FIELD = "_ordered_zone_cost_frame"
_CHOICE_FIELDS = {"hand": "discard_cards", "battlefield": "sacrifice_cards"}
_ESCAPE_CHOICE_FIELDS = {**_CHOICE_FIELDS, "graveyard": "exile_cards"}


def _bound_zone_event_order(event_id, reference):
    suffix = f":{reference}"
    if type(event_id) is not str or not event_id.endswith(suffix):
        raise ReplacementEffectError("The ordered payment event reference changed")
    fields = event_id.removesuffix(suffix).split(":")
    if len(fields) != 3 or fields[0] != "zone.change" or any(not field.isdecimal() for field in fields[1:]):
        raise ReplacementEffectError("The ordered payment event identity is malformed")
    return tuple(int(value) for value in fields[1:])


@dataclass(frozen=True, slots=True)
class OrderedZoneCostFrame:
    spell_ref: str
    option_id: str
    payments: tuple[tuple[str, str], ...]
    event_ids: tuple[tuple[str, ...], ...] | None = None
    payment_origins: tuple[tuple[str, str], ...] = ()
    event_incarnations: tuple[str, ...] = ()
    spell_incarnation: str | None = None

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.spell_ref, self.option_id)):
            raise ReplacementEffectError("The ordered cost frame needs its exact cast and option")
        if (type(self.payments) is not tuple
                or any(type(payment) is not tuple or len(payment) != 2
                       or any(type(part) is not str or not part for part in payment)
                       for payment in self.payments)):
            raise ReplacementEffectError("The ordered cost frame has malformed selected payments")
        if self.event_ids is not None:
            self._validate_granted_escape()
            return
        if len(self.payments) != 2 or self.payment_origins or self.event_incarnations or self.spell_incarnation is not None:
            raise ReplacementEffectError("The ordered cost frame requires two selected payments")
        if ({origin for origin, _ in self.payments} != set(_CHOICE_FIELDS)
                or len({ref for _, ref in self.payments}) != 2):
            raise ReplacementEffectError("The ordered cost frame requires distinct cast resources")

    def _validate_granted_escape(self):
        origins = tuple(origin for origin, _ in self.payments)
        early = self.payments[:-3]
        exile_refs = tuple(ref for _, ref in self.payments[-3:])
        if (
            origins[-3:] != ("graveyard",) * 3 or any(origin not in _CHOICE_FIELDS for origin, _ in early)
            or len({ref for _, ref in early}) != len(early) or len(set(exile_refs)) != 3
            or self.spell_ref in {ref for _, ref in self.payments}
            or type(self.event_ids) is not tuple or len(self.event_ids) > len(self.payments)
            or any(type(phase) is not tuple or not phase for phase in self.event_ids)
            or type(self.spell_incarnation) is not str or not self.spell_incarnation
            or type(self.payment_origins) is not tuple or len(self.payment_origins) != len(self.payments)
            or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str or not row[0]
                   or row[1] not in _ESCAPE_CHOICE_FIELDS for row in self.payment_origins)
            or type(self.event_incarnations) is not tuple or len(self.event_incarnations) != len(self.event_ids)
            or any(type(value) is not str or not value for value in self.event_incarnations)
        ):
            raise ReplacementEffectError("The granted Escape frame requires its earlier costs and three other cards")
        event_order = tuple(
            _bound_zone_event_order(event_id, ref)
            for phase, (_, ref) in zip(self.event_ids, self.payments)
            for event_id in phase
        )
        if any(before >= after for before, after in zip(event_order, event_order[1:])):
            raise ReplacementEffectError("The granted Escape journal must retain the payment event order")

    @classmethod
    def from_dict(cls, value):
        fields = {"schema_version", "spell_ref", "option_id", "payments"}
        if not isinstance(value, Mapping) or type(value.get("schema_version")) is not int:
            raise ReplacementEffectError("The ordered cost frame is malformed")
        version = value["schema_version"]
        if version == 2:
            fields.update({"event_ids", "payment_origins", "event_incarnations", "spell_incarnation"})
        if (version not in {1, 2} or set(value) != fields or not isinstance(value["payments"], (list, tuple))
                or any(not isinstance(payment, (list, tuple)) for payment in value["payments"])):
            raise ReplacementEffectError("The ordered cost frame is malformed")
        base = (value["spell_ref"], value["option_id"], tuple(tuple(row) for row in value["payments"]))
        if version == 1:
            return cls(*base)
        if (not isinstance(value["event_ids"], (list, tuple)) or not value["event_ids"]
                or any(not isinstance(row, (list, tuple)) for row in value["event_ids"])
                or not isinstance(value["payment_origins"], (list, tuple))
                or any(not isinstance(row, (list, tuple)) for row in value["payment_origins"])
                or not isinstance(value["event_incarnations"], (list, tuple))):
            raise ReplacementEffectError("The granted Escape frame is malformed")
        return cls(
            *base, tuple(tuple(row) for row in value["event_ids"]),
            tuple(tuple(row) for row in value["payment_origins"]), tuple(value["event_incarnations"]),
            value["spell_incarnation"],
        )

    def to_dict(self):
        value = {"schema_version": 2 if self.event_ids is not None else 1, "spell_ref": self.spell_ref,
                 "option_id": self.option_id, "payments": [list(payment) for payment in self.payments]}
        if self.event_ids is not None:
            value.update(
                event_ids=[list(row) for row in self.event_ids],
                payment_origins=[list(row) for row in self.payment_origins],
                event_incarnations=list(self.event_incarnations),
                spell_incarnation=self.spell_incarnation,
            )
        return value

    def validate_response(self, response):
        if response.get("card") != self.spell_ref or response.get("cost_option") not in (None, self.option_id):
            raise ReplacementEffectError("The continuation cast or cost option changed")
        if self.event_ids is not None:
            for origin, field in _ESCAPE_CHOICE_FIELDS.items():
                refs = tuple(ref for payment_origin, ref in self.payments if payment_origin == origin)
                selected = response.get(field, ())
                if not isinstance(selected, (tuple, list)) or tuple(selected) != refs:
                    raise ReplacementEffectError("The continuation selected different granted Escape payments")
            return
        for origin, ref in self.payments:
            selected = response.get(_CHOICE_FIELDS[origin])
            if not isinstance(selected, (list, tuple)) or tuple(selected) != (ref,):
                raise ReplacementEffectError("The continuation selected a different cost object")

    def through_pending_escape_event(self, event: ReplaceableEvent):
        if not self.event_ids or event.event_id != self.event_ids[-1][-1]:
            raise ReplacementEffectError("The pending event is not the sealed granted Escape payment")
        origin, reference = self.payments[len(self.event_ids) - 1]
        destination = "exile" if origin == "graveyard" else "graveyard"
        if (event.kind != "zone.change" or event.payload.get("origin") != origin
                or event.payload.get("destination") != destination or event.payload.get("object_ref") != reference):
            raise ReplacementEffectError("The pending replacement changed its granted Escape payment phase")
        return tuple(event_id for phase in self.event_ids for event_id in phase)

    def through_pending_payment(self, event: ReplaceableEvent):
        entry = (event.payload.get("origin"), event.payload.get("object_ref"))
        if event.kind != "zone.change" or event.payload.get("destination") != "graveyard" or entry not in self.payments:
            raise ReplacementEffectError("The pending replacement does not belong to the selected costs")
        return tuple(ref for _, ref in self.payments[:self.payments.index(entry) + 1])


class OrderedZoneCostChoiceRequired(ReplacementChoiceRequired):
    def __init__(self, required: ReplacementChoiceRequired, frame: OrderedZoneCostFrame):
        super().__init__(batch=required.batch, effects=required.effects, pending=required.pending)
        self.frame = frame
