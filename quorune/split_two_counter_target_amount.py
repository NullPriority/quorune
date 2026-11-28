"""For two counters, each legal original target count has one allocation."""

from collections.abc import Mapping
from dataclasses import dataclass


SPLIT_TWO_COUNTER_TARGET_AMOUNT_KIND = "split_two_counter_target_amount"
SPLIT_TWO_COUNTER_TARGET_CAPABILITY = "counter.producer.split_two_target_allocation"
OPTIONAL_SPLIT_TWO_COUNTER_TARGET_VERSION = 2


def is_optional_split_two_counter_amount(value):
    return (isinstance(value, Mapping) and value.get("kind") == SPLIT_TWO_COUNTER_TARGET_AMOUNT_KIND
            and type(value.get("schema_version")) is int
            and value["schema_version"] == OPTIONAL_SPLIT_TWO_COUNTER_TARGET_VERSION)


@dataclass(frozen=True, slots=True)
class SplitTwoCounterTargetAmount:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in (1, OPTIONAL_SPLIT_TWO_COUNTER_TARGET_VERSION):
            raise ValueError("Unsupported two-counter target distribution version")

    @property
    def allows_zero_targets(self):
        return self.schema_version == OPTIONAL_SPLIT_TWO_COUNTER_TARGET_VERSION

    def to_dict(self):
        return {"kind": SPLIT_TWO_COUNTER_TARGET_AMOUNT_KIND, "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"kind", "schema_version"}
                or value["kind"] != SPLIT_TWO_COUNTER_TARGET_AMOUNT_KIND):
            raise ValueError("Counter distribution requires its exact original target allocation")
        return cls(schema_version=value["schema_version"])

    def per_target(self, original_target_count):
        if type(original_target_count) is int and original_target_count == 0 and self.allows_zero_targets:
            return 0
        if type(original_target_count) is not int or original_target_count not in (1, 2):
            raise ValueError("Two-counter distribution needs one or two original targets")
        return 2 // original_target_count
