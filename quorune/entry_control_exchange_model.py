from __future__ import annotations

"""The closed target relation for an exchange with an original entrant."""

from collections.abc import Mapping
from dataclasses import dataclass

from .target_forms import SharedPermanentTypeScope


ENTRY_EXCHANGE_TARGET_FIELD = "entry_exchange_target"
ENTRY_EXCHANGE_CAPABILITY = "target.event_object.delegated_shared_type_exchange"
ENTRY_EXCHANGE_TYPES = frozenset({"artifact", "creature", "enchantment"})
_PERMANENT_TYPES = SharedPermanentTypeScope.PERMANENT.types


@dataclass(frozen=True, slots=True)
class EntryExchangeTarget:
    controller: str | None
    types: tuple[str, ...]

    def __post_init__(self):
        if self.controller is not None and (
            type(self.controller) is not str or not self.controller or self.controller.startswith("$")
        ):
            raise ValueError("An entry exchange requires its actual entrant controller")
        if (not isinstance(self.types, tuple) or any(type(value) is not str for value in self.types)
                or self.types != tuple(sorted(set(self.types))) or not set(self.types) <= _PERMANENT_TYPES
                or self.controller is None and self.types):
            raise ValueError("An entry exchange requires canonical permanent types")

    def to_dict(self):
        return {"controller": self.controller, "types": list(self.types)}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"controller", "types"}
                or not isinstance(value["types"], (list, tuple))):
            raise ValueError("Malformed bound entry exchange target")
        return cls(value["controller"], tuple(value["types"]))

    def matches(self, *, controller, types):
        return bool(self.controller and controller and controller != self.controller
                    and not set(self.types).isdisjoint(types))


def entry_exchange_event_condition():
    return {"field": "types", "op": "contains_any", "value": sorted(ENTRY_EXCHANGE_TYPES)}
