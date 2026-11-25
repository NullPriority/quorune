"""The closed end-of-combat result and its live public recipients."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

END_COMBAT_COUNTERPART_OP = "resolve_end_combat_block_counterparts"
END_COMBAT_COUNTERPART_FIELD = "end_combat_block_counterparts"
END_COMBAT_COUNTERPART_TEMPLATE = "fixed-end-combat-counterpart-result-v1"
END_COMBAT_COUNTERPART_CAPABILITY = "resolution.combat.live_block_counterparts"


def end_combat_counterpart_condition() -> dict[str, str]:
    return {"field": "step", "op": "eq", "value": "end_combat"}


class EndCombatCounterpartResult(StrEnum):
    DESTROY = "destroy"
    PLACE_MINUS_ZERO_MINUS_TWO_COUNTER = "place_minus_zero_minus_two_counter"


@dataclass(frozen=True, slots=True)
class EndCombatCounterpartSpec:
    result: EndCombatCounterpartResult

    def __post_init__(self) -> None:
        if not isinstance(self.result, EndCombatCounterpartResult):
            raise ValueError("An end-of-combat counterpart result requires a closed operation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": END_COMBAT_COUNTERPART_OP, "schema_version": 1,
            "source": "$source", "result": self.result.value,
        }

    @property
    def operation(self) -> str:
        if self.result is EndCombatCounterpartResult.DESTROY:
            return "destroy_targets"
        return "place_counters_on_targets"

    @property
    def capabilities(self) -> tuple[str, ...]:
        result_owners = (
            ("permanent.destroy.effect", "resolution.effect.fixed_homogeneous_target_set")
            if self.result is EndCombatCounterpartResult.DESTROY
            else ("counter.producer.fixed_permanent_target_set_effect",)
        )
        return (
            END_COMBAT_COUNTERPART_CAPABILITY, "combat.declaration.typed_components",
            "trigger.placement.apnap", *result_owners,
        )

    def to_effect(self) -> dict[str, Any]:
        return {"op": self.operation, END_COMBAT_COUNTERPART_FIELD: self.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EndCombatCounterpartSpec":
        if (not isinstance(value, Mapping) or set(value) != {"op", "schema_version", "source", "result"}
                or value["op"] != END_COMBAT_COUNTERPART_OP or value["source"] != "$source"
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("End-of-combat counterparts require an exact resolving-source descriptor")
        return cls(EndCombatCounterpartResult(value["result"]))

    @classmethod
    def from_effect(cls, value: Mapping[str, Any]) -> "EndCombatCounterpartSpec":
        if not isinstance(value, Mapping) or set(value) != {"op", END_COMBAT_COUNTERPART_FIELD}:
            raise ValueError("A combat counterpart result requires one closed instruction")
        spec = cls.from_dict(value[END_COMBAT_COUNTERPART_FIELD])
        if value["op"] != spec.operation:
            raise ValueError("The combat counterpart instruction must match its result owner")
        return spec
