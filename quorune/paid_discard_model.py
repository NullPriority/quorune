"""Immutable characteristics of the card committed to a fixed discard cost."""

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


PAID_DISCARD_CONTEXT = "paid_discard_characteristics"


@dataclass(frozen=True, slots=True)
class PaidDiscardSnapshot:
    logical_object_id: str
    is_land: bool | None
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.logical_object_id) is not str or not self.logical_object_id
                or (self.is_land is not None and type(self.is_land) is not bool)):
            raise ValueError("A paid discard requires its original hand incarnation and type")

    def to_dict(self):
        return {"schema_version": self.schema_version,
                "logical_object_id": self.logical_object_id, "is_land": self.is_land}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "logical_object_id", "is_land"}:
            raise ValueError("Paid discard characteristics have a closed receipt schema")
        return cls(**dict(value))


_DISCARD_REVEAL_WITNESS: ContextVar[tuple[str, set[str]] | None] = ContextVar("paid_discard_reveal_witness", default=None)


@contextmanager
def observe_paid_discard_reveal(logical_object_id, revealed_ids):
    token = _DISCARD_REVEAL_WITNESS.set((logical_object_id, revealed_ids) if revealed_ids is not None else None)
    try:
        yield
    finally:
        _DISCARD_REVEAL_WITNESS.reset(token)


def record_paid_discard_replacement_reveal(logical_object_id):
    active = _DISCARD_REVEAL_WITNESS.get()
    if active is not None and active[0] == logical_object_id:
        active[1].add(logical_object_id)
