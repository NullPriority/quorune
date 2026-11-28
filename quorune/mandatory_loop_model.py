from __future__ import annotations

"""Durable recurrence evidence for proven mandatory linked actions."""

from dataclasses import dataclass
from typing import Mapping


LINKED_EXILE_LOOP_FAMILY = "linked_exile"
RECEIVED_DAMAGE_LOOP_FAMILY = "received_damage"
_FAMILIES = frozenset({LINKED_EXILE_LOOP_FAMILY, RECEIVED_DAMAGE_LOOP_FAMILY})


@dataclass(frozen=True, slots=True)
class MandatoryLinkedLoopWatch:
    """Bounded recurrence evidence for ordinary forced linked triggers."""

    anchor: str | None = None
    cursor: str | None = None
    stride: int = 1
    distance: int = 0
    schema_version: int = 1
    family: str | None = None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise ValueError("Unsupported mandatory loop witness schema")
        if self.schema_version == 2 and self.anchor is not None:
            if type(self.family) is not str or self.family not in _FAMILIES:
                raise ValueError("An active mandatory loop witness requires a proof family")
        elif self.family is not None:
            raise ValueError("Only an active version-two loop witness carries a proof family")
        if self.anchor is None:
            if self.cursor is not None or type(self.stride) is not int or self.stride != 1 or type(self.distance) is not int or self.distance != 0:
                raise ValueError("An empty loop witness cannot retain progress")
            return
        if (type(self.anchor) is not str or len(self.anchor) != 64 or any(c not in "0123456789abcdef" for c in self.anchor)
                or type(self.cursor) is not str or not self.cursor
                or type(self.stride) is not int or self.stride < 1 or self.stride & (self.stride - 1)
                or type(self.distance) is not int or not 0 <= self.distance < self.stride):
            raise ValueError("Mandatory linked loop witness is malformed")

    def cleared(self) -> MandatoryLinkedLoopWatch:
        return MandatoryLinkedLoopWatch(schema_version=self.schema_version)

    def observe(self, signature: str, cursor: str, *,
                family: str = LINKED_EXILE_LOOP_FAMILY) -> tuple[MandatoryLinkedLoopWatch, bool]:
        """Brent's recurrence test uses one anchor regardless of cycle length."""
        if (
            type(family) is not str or family not in _FAMILIES
            or self.schema_version == 1 and family != LINKED_EXILE_LOOP_FAMILY
        ):
            raise ValueError("The loop witness does not support this proof family")
        stored_family = family if self.schema_version == 2 else None
        if self.anchor is None or stored_family != self.family:
            return MandatoryLinkedLoopWatch(signature, cursor, schema_version=self.schema_version,
                                            family=stored_family), False
        if cursor == self.cursor:
            return self, False
        if signature == self.anchor:
            return self, True
        distance = self.distance + 1
        if distance == self.stride:
            return MandatoryLinkedLoopWatch(signature, cursor, self.stride * 2, 0,
                                            self.schema_version, stored_family), False
        return MandatoryLinkedLoopWatch(self.anchor, cursor, self.stride, distance,
                                        self.schema_version, stored_family), False

    def to_dict(self):
        value = {"schema_version": self.schema_version, "anchor": self.anchor, "cursor": self.cursor,
                 "stride": self.stride, "distance": self.distance}
        if self.schema_version == 2:
            value["family"] = self.family
        return value

    @classmethod
    def from_dict(cls, value):
        fields = {"schema_version", "anchor", "cursor", "stride", "distance"}
        if not isinstance(value, Mapping) or type(value.get("schema_version")) is not int:
            raise ValueError("Unsupported mandatory loop witness schema")
        if value["schema_version"] == 2:
            fields.add("family")
        if set(value) != fields:
            raise ValueError("Unsupported mandatory loop witness schema")
        return cls(**value)
