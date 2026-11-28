"""Immutable owner-scoped receipts for simultaneous graveyard arrivals."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .continuous_effect_model import ContinuousObjectIdentity


@dataclass(frozen=True, slots=True)
class GraveyardArrivalGroup:
    owner: str
    members: tuple[tuple[str, ContinuousObjectIdentity], ...]

    def __post_init__(self):
        if type(self.owner) is not str or not self.owner or len(self.members) < 2:
            raise ValueError("A graveyard order group requires its owner and multiple arrivals")
        refs = set()
        objects = set()
        for ref, identity in self.members:
            if type(ref) is not str or not ref or not isinstance(identity, ContinuousObjectIdentity):
                raise ValueError("A graveyard order group requires exact original card references")
            if ref in refs or identity.object_id in objects:
                raise ValueError("A graveyard order group cannot repeat an arrival")
            refs.add(ref)
            objects.add(identity.object_id)

    def to_dict(self):
        return {
            "owner": self.owner,
            "members": [{"ref": ref, "identity": identity.to_dict()} for ref, identity in self.members],
        }

    @classmethod
    def from_dict(cls, data):
        if (not isinstance(data, Mapping) or set(data) != {"owner", "members"}
                or not isinstance(data["members"], (list, tuple))):
            raise ValueError("Graveyard arrival group fields changed")
        members = []
        for row in data["members"]:
            if not isinstance(row, Mapping) or set(row) != {"ref", "identity"}:
                raise ValueError("A graveyard order member requires one typed card reference")
            members.append((row["ref"], ContinuousObjectIdentity.from_dict(row["identity"])))
        return cls(data["owner"], tuple(members))

    @property
    def refs(self):
        return tuple(ref for ref, _ in self.members)

    def ordered_ids(self, refs: Sequence[str]) -> tuple[str, ...]:
        if (not isinstance(refs, (tuple, list)) or len(refs) != len(self.members)
                or any(type(ref) is not str for ref in refs) or set(refs) != set(self.refs)):
            raise ValueError("Order every card entering your graveyard exactly once")
        by_ref = dict(self.members)
        return tuple(by_ref[ref].object_id for ref in refs)


@dataclass(frozen=True, slots=True)
class GraveyardArrivalOrders:
    groups: tuple[GraveyardArrivalGroup, ...]
    choices: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self):
        if (not isinstance(self.groups, tuple)
                or any(not isinstance(group, GraveyardArrivalGroup) for group in self.groups)
                or len({group.owner for group in self.groups}) != len(self.groups)
                or not isinstance(self.choices, tuple) or len(self.choices) > len(self.groups)):
            raise ValueError("Graveyard orders require the exact owner groups")
        ids = [identity.object_id for group in self.groups for _, identity in group.members]
        refs = [ref for group in self.groups for ref in group.refs]
        if len(ids) != len(set(ids)) or len(refs) != len(set(refs)):
            raise ValueError("An arrival cannot belong to multiple graveyards")
        for group, refs in zip(self.groups, self.choices):
            group.ordered_ids(refs)

    def to_dict(self):
        return {
            "schema_version": 1,
            "groups": [group.to_dict() for group in self.groups],
            "choices": [list(order) for order in self.choices],
        }

    @classmethod
    def from_dict(cls, data):
        if (not isinstance(data, Mapping) or set(data) != {"schema_version", "groups", "choices"}
                or type(data["schema_version"]) is not int or data["schema_version"] != 1
                or not isinstance(data["groups"], (list, tuple))
                or not isinstance(data["choices"], (list, tuple))
                or any(not isinstance(choice, (list, tuple)) for choice in data["choices"])):
            raise ValueError("Graveyard arrival orders require their closed version")
        return cls(tuple(GraveyardArrivalGroup.from_dict(group) for group in data["groups"]),
                   tuple(tuple(order) for order in data["choices"]))

    @property
    def current_group(self) -> GraveyardArrivalGroup | None:
        return self.groups[len(self.choices)] if len(self.choices) < len(self.groups) else None

    def submit(self, actor: str, refs: Sequence[str]) -> "GraveyardArrivalOrders":
        group = self.current_group
        if group is None or actor != group.owner:
            raise ValueError("Only the current graveyard owner can submit this order")
        group.ordered_ids(refs)
        return GraveyardArrivalOrders(self.groups, (*self.choices, tuple(refs)))


GRAVEYARD_ARRIVAL_ORDER_CAPABILITY = "zone.graveyard.ordered_simultaneous_arrival"
