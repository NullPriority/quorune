"""Authoritative restart frame for priority actions with whole-zone costs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ...replacement.immutable import FrozenMap, thaw_value
from ...replacement.model import ReplacementEffectError
from ...replacement.ordering import ReplacementChoiceRequired


WHOLE_ZONE_COST_FRAME_FIELD = "_whole_zone_cost_frame"
WHOLE_ZONE_SELECTION_FIELDS = frozenset({"cost_cards", "discard_cards", "sacrifice_cards"})
_TRANSITIONS = {"hand": "discard", "battlefield": "sacrifice"}


@dataclass(frozen=True, slots=True)
class WholeZonePaymentGroup:
    origin: str
    members: tuple[tuple[str, str, str], ...]
    selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self):
        if type(self.origin) is not str or self.origin not in _TRANSITIONS or any(
            len(member) != 3 or any(type(value) is not str or not value for value in member)
            for member in self.members
        ):
            raise ReplacementEffectError("Automatic zone cost requires current object identities")
        if any(len(set(column)) != len(column) for column in zip(*self.members)):
            raise ReplacementEffectError("Automatic zone cost requires distinct objects")
        if (not self.members and self.selections) or any(
            not (type(value) is str and value or isinstance(value, FrozenMap)) for value in self.selections
        ):
            raise ReplacementEffectError("Automatic zone cost replacement choices are malformed")

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"origin", "members", "selections"}
                or not isinstance(value["members"], (list, tuple))
                or any(not isinstance(member, (list, tuple)) for member in value["members"])
                or not isinstance(value["selections"], (list, tuple))):
            raise ReplacementEffectError("Automatic zone cost group is malformed")
        return cls(
            value["origin"], tuple(tuple(member) for member in value["members"]),
            tuple(FrozenMap(selection) if isinstance(selection, Mapping) else selection
                  for selection in value["selections"]),
        )

    def to_dict(self):
        return {"origin": self.origin, "members": [list(member) for member in self.members],
                "selections": [thaw_value(selection) for selection in self.selections]}


@dataclass(frozen=True, slots=True)
class WholeZoneCostFrame:
    spell_ref: str
    option_id: str
    groups: tuple[WholeZonePaymentGroup, ...]

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.spell_ref, self.option_id))
                or not 1 <= len(self.groups) <= 2
                or len({group.origin for group in self.groups}) != len(self.groups)
                or not self.groups[-1].members):
            raise ReplacementEffectError("Automatic zone cost frame requires its cast and pending group")

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "spell_ref", "option_id", "groups"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not isinstance(value["groups"], (list, tuple))):
            raise ReplacementEffectError("Automatic zone cost frame is malformed")
        return cls(value["spell_ref"], value["option_id"],
                   tuple(WholeZonePaymentGroup.from_dict(group) for group in value["groups"]))

    def to_dict(self):
        return {"schema_version": 1, "spell_ref": self.spell_ref, "option_id": self.option_id,
                "groups": [group.to_dict() for group in self.groups]}

    def validate_response(self, response):
        if (response.get("card") != self.spell_ref or response.get("cost_option") not in (None, self.option_id)
                or WHOLE_ZONE_SELECTION_FIELDS.intersection(response)):
            raise ReplacementEffectError("Automatic zone cost continuation changed its cast or inputs")

    def append_selection(self, selection):
        choice = FrozenMap(selection) if isinstance(selection, Mapping) else selection
        last = self.groups[-1]
        return replace(self, groups=(*self.groups[:-1], replace(last, selections=(*last.selections, choice))))

    def replacement_event_ids(self, batch, *, seat):
        group = self.groups[-1]
        identities = {ref: (oid, logical_id) for oid, logical_id, ref in group.members}
        seen = set()
        for event in batch.events:
            payload = event.payload
            ref = payload.get("object_ref")
            affected = event.affected_object
            if (event.kind != "zone.change" or payload.get("origin") != group.origin or type(ref) is not str or ref not in identities
                    or affected is None or (affected.object_id, payload.get("logical_object_id")) != identities[ref]
                    or payload.get("transition_kind") != _TRANSITIONS[group.origin]
                    or (affected.controller if group.origin == "battlefield" else affected.owner) != seat
                    or ref in seen or not event.event_id.startswith("zone.change:")
                    or not event.event_id.endswith(f":{ref}")):
                raise ReplacementEffectError("Replacement event does not belong to the automatic payment group")
            seen.add(ref)
        if not seen:
            raise ReplacementEffectError("An automatic payment replacement requires a pending event")
        return {event.event_id for event in batch.events}


class WholeZoneCostChoiceRequired(ReplacementChoiceRequired):
    def __init__(self, required: ReplacementChoiceRequired, frame: WholeZoneCostFrame):
        super().__init__(batch=required.batch, effects=required.effects, pending=required.pending)
        self.frame = frame


def whole_zone_cost_event_ids(batch, response, *, action, seat):
    frame = WholeZoneCostFrame.from_dict(response[WHOLE_ZONE_COST_FRAME_FIELD])
    if action == "activate":
        from ..activation.whole_hand_mana_costs import validate_activation_frame

        validate_activation_frame(frame, response)
    elif action == "cast":
        frame.validate_response(response)
    else:
        raise ReplacementEffectError("Automatic zone costs require their exact priority action")
    return frame.replacement_event_ids(batch, seat=seat)
