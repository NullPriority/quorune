from __future__ import annotations

"""Match-long English name counts at the committed spell-cast boundary."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from .replacement.immutable import FrozenMap, thaw_value


SPELL_CAST_NAME_HISTORY_CAPABILITY = "history.spell_cast.names"
CAST_NAME_RECEIPT_FIELD = "committed_cast_name_receipt"


def _name(value):
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError("Spell cast history requires a canonical English name")
    return value


@dataclass(frozen=True, slots=True)
class SpellCastNameHistory:
    counts_by_caster: FrozenMap = field(default_factory=FrozenMap)
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported spell cast name history version")
        if not isinstance(self.counts_by_caster, Mapping):
            raise ValueError("Spell cast history requires named counts per caster")
        for caster, counts in self.counts_by_caster.items():
            if type(caster) is not str or not caster or not isinstance(counts, Mapping) or not counts:
                raise ValueError("Spell cast history requires a caster and recorded names")
            for name, count in counts.items():
                _name(name)
                if type(count) is not int or count < 1:
                    raise ValueError("Named cast counts must be positive integers")
        object.__setattr__(self, "counts_by_caster", FrozenMap(self.counts_by_caster))

    def to_dict(self):
        return {"schema_version": self.schema_version, "counts_by_caster": thaw_value(self.counts_by_caster)}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, Mapping) or set(data) != {"schema_version", "counts_by_caster"}:
            raise ValueError("Spell cast name history has a closed schema")
        return cls(**dict(data))

    def note_cast(self, *, caster, names):
        if not names:
            return self
        counts = dict(self.counts_by_caster.get(caster, {}))
        for name in names:
            counts[_name(name)] = counts.get(name, 0) + 1
        return SpellCastNameHistory(FrozenMap({**dict(self.counts_by_caster), caster: counts}))

    def count(self, caster, name):
        return self.counts_by_caster.get(caster, {}).get(_name(name), 0)


@dataclass(frozen=True, slots=True)
class SpellCastNameReceipt:
    stack_ref: str
    caster: str
    hand_owner: str | None
    names: tuple[str, ...]

    def __post_init__(self):
        if (type(self.stack_ref) is not str or not self.stack_ref
                or type(self.caster) is not str or not self.caster
                or self.hand_owner is not None and (type(self.hand_owner) is not str or not self.hand_owner)
                or not self.names or self.names != tuple(sorted({_name(name) for name in self.names}))):
            raise ValueError("A committed cast receipt needs its original spell, caster and names")

    def to_dict(self):
        return {"schema_version": 1, "stack_ref": self.stack_ref, "caster": self.caster,
                "hand_owner": self.hand_owner, "names": list(self.names)}

    @classmethod
    def from_dict(cls, data):
        if (not isinstance(data, Mapping) or set(data) != {"schema_version", "stack_ref", "caster", "hand_owner", "names"}
                or type(data["schema_version"]) is not int or data["schema_version"] != 1
                or not isinstance(data["names"], (list, tuple))):
            raise ValueError("A committed cast receipt has a closed schema")
        return cls(data["stack_ref"], data["caster"], data["hand_owner"], tuple(data["names"]))
