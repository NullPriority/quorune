"""Explicit dependency on the one optionally announced original target."""
from collections.abc import Mapping
from dataclasses import dataclass

OPTIONAL_TARGET_RESULT_FIELD = "optional_target_result"
OPTIONAL_TARGET_RESULT_CAPABILITY = "resolution.effect_program.optional_target_result"
OPTIONAL_TARGET_RESULT_MECHANIC = "fixed-optional-target-characteristic-result"
OPTIONAL_TARGET_RESULT_TEMPLATE = "optional-target-characteristic-result-v1"


@dataclass(frozen=True, slots=True)
class OptionalTargetResultSpec:
    index: int = 0
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.index) is not int or self.index != 0):
            raise ValueError("Optional target result requires explicit version one and target zero")

    def to_dict(self):
        return {"schema_version":1,"index":0}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value,Mapping) or set(value) != {"schema_version","index"}:
            raise ValueError("Optional target dependency fields are closed")
        return cls(**dict(value))

    def target_is_absent(self, targets):
        if not isinstance(targets,(list,tuple)) or len(targets)>1:
            raise ValueError("Optional result requires zero or one announced target")
        if targets and targets[0] is not None and (type(targets[0]) is not str or not targets[0]):
            raise ValueError("Optional result target reference is malformed")
        return not targets or targets[0] is None
