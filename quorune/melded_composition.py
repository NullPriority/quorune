"""Physical combined-face data for the existing permanent composition."""

from collections.abc import Mapping
from dataclasses import dataclass

from .copiable_program_source import COPIABLE_ORACLE_FIELD
from .replacement.immutable import FrozenMap, thaw_value


@dataclass(frozen=True, slots=True)
class MeldedCompositionFace:
    component_ids: tuple[str, str]
    oracle_id: str
    characteristics: FrozenMap
    timestamp: int

    def __post_init__(self):
        if (not isinstance(self.component_ids, (list, tuple)) or len(self.component_ids) != 2
                or any(type(item) is not str or not item for item in self.component_ids)
                or len(set(self.component_ids)) != 2 or type(self.oracle_id) is not str or not self.oracle_id
                or not isinstance(self.characteristics, Mapping)
                or self.characteristics.get(COPIABLE_ORACLE_FIELD) != self.oracle_id
                or self.characteristics.get("mana_cost") != ""
                or type(self.timestamp) is not int or self.timestamp < 1):
            raise ValueError("A combined meld face needs two distinct physical parts and its exact native characteristics")
        object.__setattr__(self, "component_ids", tuple(self.component_ids))
        object.__setattr__(self, "characteristics", FrozenMap(self.characteristics))

    def to_dict(self):
        return {"component_ids": list(self.component_ids), "oracle_id": self.oracle_id,
                "characteristics": thaw_value(self.characteristics), "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"component_ids", "oracle_id", "characteristics", "timestamp"}:
            raise ValueError("A combined meld face has a closed storage record")
        return cls(value["component_ids"], value["oracle_id"], value["characteristics"], value["timestamp"])
