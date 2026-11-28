"""Closed private land discard selected during an entry replacement."""

from collections.abc import Mapping
from dataclasses import dataclass

CAPABILITY = "zone.entry.discard_land_or_graveyard"
HANDLER = "replacement.zone.entry-land-discard.v1"
FIELD = "entry_land_discard"
REQUIREMENTS = (CAPABILITY, "zone.change.destination_replacement")


def descriptor():
    return {"handler_id": HANDLER, "schema_version": 1, "event": "zone.change"}


@dataclass(frozen=True, slots=True)
class EntryLandDiscard:
    object_id: str
    ref: str
    incarnation: str
    actor: str

    def __post_init__(self):
        if any(type(value) is not str or not value for value in
               (self.object_id, self.ref, self.incarnation, self.actor)):
            raise ValueError("A land discard needs its private hand incarnation and actor")

    def to_dict(self):
        return {"object_id": self.object_id, "ref": self.ref, "incarnation": self.incarnation, "actor": self.actor}

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, Mapping) or set(raw) != {"object_id", "ref", "incarnation", "actor"}:
            raise ValueError("An entry land discard has a closed selection")
        return cls(**raw)

    def current_card(self, host):
        card = host.state.cards.get(self.object_id)
        if (card is None or not card.is_card_object or card.owner != self.actor or card.zone != "hand"
                or card.ref != self.ref or card.logical_object_id != self.incarnation):
            raise ValueError("The selected entry land discard has left its captured hand incarnation")
        data = host._effective_card_data(card)
        if "land" not in host._type_parts(str(data.get("type_line") or ""))[0]:
            raise ValueError("The selected entry discard must still be a land card")
        return card


def entry_land_discard_applicable(event, operations):
    from ..replacement.operations import SetField

    for operation in operations:
        if isinstance(operation, SetField) and operation.field == FIELD and operation.value != "decline":
            chosen = EntryLandDiscard.from_dict(operation.value)
            scope = event.entry_scope
            return scope is not None and bool(scope.eligible_zone_choices((chosen.object_id,)))
    return True


def paid_selection(event):
    value = event.payload.get(FIELD)
    return None if value is None or value == "decline" else EntryLandDiscard.from_dict(value)


def require_prepared_discard(parent, plan):
    from ..zone_trigger_events import ZoneTransitionKind

    paid = paid_selection(parent.event)
    if (paid is None or plan.object_id != paid.object_id or plan.logical_object_id != paid.incarnation
            or plan.event.payload.get("object_ref") != paid.ref or plan.origin != "hand"
            or plan.requested_destination != "graveyard" or plan.state_revision != parent.state_revision
            or plan.transition_kind is not ZoneTransitionKind.DISCARD or plan.entry_land_discard is not None):
        raise ValueError("Prepared entry discard does not match its exact hand selection")
    return paid
