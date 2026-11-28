"""The names and controller of the original spell targeted by an exile scan."""

from collections.abc import Mapping
from dataclasses import dataclass


NAME_SCAN_CAPABILITY = "stack.counter.different_name_exile_cast"
NAME_SCAN_MECHANIC = "stack-counter-different-name-exile-cast"
NAME_SCAN_OPERATION = "exile_until_differently_named_nonland"
NAME_SCAN_STEP_OPERATION = "exile_until_differently_named_nonland_step"
NAME_SCAN_CAST_OPERATION = "exile_until_differently_named_nonland_cast"
NAME_SCAN_FINISH_OPERATION = "exile_until_differently_named_nonland_finish"


@dataclass(frozen=True, slots=True)
class StackSpellNameScan:
    resolution_ref: str
    target_ref: str
    actor: str
    excluded_names: tuple[str, ...]

    def __post_init__(self):
        refs = (self.resolution_ref, self.target_ref, self.actor)
        if any(type(value) is not str or not value or value.startswith("$") for value in refs):
            raise ValueError("A different-name exile scan requires its original spell, target and player")
        if self.resolution_ref == self.target_ref:
            raise ValueError("A different-name exile scan requires another spell")
        names = self.excluded_names
        if (not isinstance(names, tuple)
                or any(type(name) is not str or not name or name != name.strip() for name in names)
                or names != tuple(sorted(set(names)))):
            raise ValueError("Target spell names must be unique canonical name characteristics")

    def to_dict(self):
        return {
            "resolution_ref": self.resolution_ref,
            "target_ref": self.target_ref,
            "actor": self.actor,
            "excluded_names": list(self.excluded_names),
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"resolution_ref", "target_ref", "actor", "excluded_names"}
        if (not isinstance(value, Mapping) or set(value) != fields
                or not isinstance(value["excluded_names"], (list, tuple))):
            raise ValueError("A different-name exile scan requires its closed spell identity")
        return cls(value["resolution_ref"], value["target_ref"], value["actor"], tuple(value["excluded_names"]))

    def require_resolution(self, *, actor, stack_ref, targets):
        if (self.actor, self.resolution_ref, (self.target_ref,)) != (actor, stack_ref, tuple(targets)):
            raise ValueError("A different-name exile scan lost its original target and affected player")

    def qualified(self, *, types, names):
        return bool(names) and "land" not in types and set(self.excluded_names).isdisjoint(names)

    def effect(self):
        return {"op": NAME_SCAN_OPERATION, "schema_version": 1, "player": self.actor, "scan": self.to_dict()}


def parse_name_scan_effect(effect):
    if (set(effect) != {"op", "schema_version", "player", "scan"}
            or effect["op"] != NAME_SCAN_OPERATION or type(effect["schema_version"]) is not int
            or effect["schema_version"] != 1):
        raise ValueError("A different-name exile scan requires a closed bound operation")
    scan = StackSpellNameScan.from_dict(effect["scan"])
    if scan.actor != effect["player"]:
        raise ValueError("A different-name exile scan changed the captured player")
    return scan
