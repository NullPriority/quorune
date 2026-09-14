from __future__ import annotations

"""Cast-option integration for fixed public alternative costs."""

import copy
from collections.abc import Mapping
from typing import Any, Protocol

from ...compiled_public_alternative_costs import (
    compiled_fixed_public_alternative_cost_specs,
)
from ...public_alternative_costs import (
    fixed_public_alternative_condition_met,
)


class FixedPublicAlternativeCostHost(Protocol):
    state: Any
    semantics: Any

    def card_record(self, card: Any) -> Any: ...

    def _effective_static_component_keys(
        self, card: Any
    ) -> tuple[str, ...]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _alternate_cost_condition_met(
        self, seat: str, condition: Mapping[str, Any]
    ) -> bool: ...


def alternative_cost_condition_met(
    host: FixedPublicAlternativeCostHost,
    seat: str,
    card: Any,
    condition: Mapping[str, Any],
) -> bool:
    """Dispatch typed public conditions without changing legacy ownership."""

    if "kind" in condition:
        return fixed_public_alternative_condition_met(
            host, seat, card, condition
        )
    return host._alternate_cost_condition_met(seat, condition)


def with_fixed_public_alternative_costs(
    host: FixedPublicAlternativeCostHost,
    card: Any,
    schema: Mapping[str, Any],
    *,
    suppress_source_costs: bool,
) -> dict[str, Any] | None:
    """Add one current complete-card-admitted alternative cost."""

    result = copy.deepcopy(dict(schema))
    specs = (
        compiled_fixed_public_alternative_cost_specs(host, card)
        if not suppress_source_costs
        else ()
    )
    if not specs:
        return result
    if len(specs) != 1 or any(
        result.get(field)
        for field in ("additional_costs", "alternate_costs", "optional_costs")
    ):
        return None
    result["alternate_costs"] = [specs[0].cast_cost_option()]
    return result


__all__ = [
    "alternative_cost_condition_met",
    "FixedPublicAlternativeCostHost",
    "with_fixed_public_alternative_costs",
]
