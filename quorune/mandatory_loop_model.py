from __future__ import annotations

"""Durable recurrence evidence for proven mandatory linked actions."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class MandatoryLinkedLoopWatch:
    """Bounded recurrence evidence for ordinary forced linked triggers."""

    anchor: str | None = None
    cursor: str | None = None
    stride: int = 1
    distance: int = 0

    def __post_init__(self):
        if self.anchor is None:
            if self.cursor is not None or type(self.stride) is not int or self.stride != 1 or type(self.distance) is not int or self.distance != 0:
                raise ValueError("An empty loop witness cannot retain progress")
            return
        if (type(self.anchor) is not str or len(self.anchor) != 64 or any(c not in "0123456789abcdef" for c in self.anchor)
                or type(self.cursor) is not str or not self.cursor
                or type(self.stride) is not int or self.stride < 1 or self.stride & (self.stride - 1)
                or type(self.distance) is not int or not 0 <= self.distance < self.stride):
            raise ValueError("Mandatory linked loop witness is malformed")

    def observe(self, signature: str, cursor: str) -> tuple[MandatoryLinkedLoopWatch, bool]:
        """Brent's recurrence test uses one anchor regardless of cycle length."""
        if cursor == self.cursor:
            return self, False
        if self.anchor is None:
            return MandatoryLinkedLoopWatch(signature, cursor), False
        if signature == self.anchor:
            return self, True
        distance = self.distance + 1
        if distance == self.stride:
            return MandatoryLinkedLoopWatch(signature, cursor, self.stride * 2), False
        return MandatoryLinkedLoopWatch(self.anchor, cursor, self.stride, distance), False

    def to_dict(self):
        return {"schema_version": 1, "anchor": self.anchor, "cursor": self.cursor,
                "stride": self.stride, "distance": self.distance}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "anchor", "cursor", "stride", "distance"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Unsupported mandatory linked loop witness schema")
        return cls(value["anchor"], value["cursor"], value["stride"], value["distance"])
