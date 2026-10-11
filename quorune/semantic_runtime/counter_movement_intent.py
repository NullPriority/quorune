from __future__ import annotations

"""Immutable identity-pinned CR 122.5 counter movement."""

from dataclasses import dataclass
from ..counter_names import normalized_counter_name
from ..replacement.immutable import FrozenMap


def decode_counter_movement_identity(value):
    fields = {"actor", "from_ref", "from_identity", "to_ref", "to_identity",
              "counter_name", "amount", "reason", "source_ref"}
    from collections.abc import Mapping
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("Counter movement continuation identity is malformed")
    return MoveCountersIntent(**value).identity()


@dataclass(frozen=True, slots=True)
class MoveCountersIntent:
    actor: str
    from_ref: str
    from_identity: str
    to_ref: str
    to_identity: str
    counter_name: str
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        from .intents import _freeze_replacement_selections
        for value in (self.actor, self.from_ref, self.from_identity,
                      self.to_ref, self.to_identity, self.reason):
            if type(value) is not str or not value:
                raise ValueError("Counter movement requires pinned nonempty identities")
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError("Counter movement requires a positive exact amount")
        if self.source_ref is not None and (type(self.source_ref) is not str or not self.source_ref):
            raise ValueError("Counter movement source reference must be nonempty")
        object.__setattr__(self, "counter_name", normalized_counter_name(self.counter_name))
        object.__setattr__(self, "replacement_selections", _freeze_replacement_selections(
            tuple(self.replacement_selections), family="Counter movement"))

    def identity(self) -> dict[str, object]:
        return {key: getattr(self, key) for key in (
            "actor", "from_ref", "from_identity", "to_ref", "to_identity",
            "counter_name", "amount", "reason", "source_ref")}
