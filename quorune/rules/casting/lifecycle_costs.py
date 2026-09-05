from __future__ import annotations

"""Cast-option composition for fixed public casting lifecycles."""

import copy
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ...cast_lifecycles import FixedCastLifecycleKind
from ...compiled_cast_lifecycles import (
    compiled_fixed_cast_lifecycle_spec,
    compiled_fixed_cast_lifecycle_specs,
)


class FixedCastLifecycleCostHost(Protocol):
    semantics: Any

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def with_fixed_cast_lifecycle_costs(
    host: FixedCastLifecycleCostHost,
    card: Any,
    schema: Mapping[str, Any],
    *,
    suppress_source_costs: bool,
) -> dict[str, Any] | None:
    """Add fixed Buyback, Dash, and Warp to the existing option schema."""

    result = copy.deepcopy(dict(schema))
    specs = (
        compiled_fixed_cast_lifecycle_specs(host, card)
        if not suppress_source_costs
        else ()
    )
    optional_costs = [
        dict(value) for value in result.get("optional_costs", ())
    ]
    alternate_costs = [
        dict(value) for value in result.get("alternate_costs", ())
    ]
    existing_ids = {
        str(value.get("id") or "")
        for value in (*optional_costs, *alternate_costs)
    }
    for spec in specs:
        if spec.kind in {
            FixedCastLifecycleKind.MADNESS,
            FixedCastLifecycleKind.RETRACE,
        }:
            continue
        option = spec.fixed_cost_option()
        if option["id"] in existing_ids:
            return None
        existing_ids.add(option["id"])
        if spec.kind is FixedCastLifecycleKind.BUYBACK:
            optional_costs.append(option)
        else:
            alternate_costs.append(option)
    result["optional_costs"] = optional_costs
    result["alternate_costs"] = alternate_costs
    return result


def retrace_base_options(
    host: FixedCastLifecycleCostHost,
    card: Any,
    printed: Sequence[Mapping[str, Any]],
    *,
    cast_without_mana: bool,
    force_without_mana_cost: bool,
    suppress_source_costs: bool,
) -> list[dict[str, Any]]:
    """Decorate only the printed normal cost with Retrace's land payment."""

    if (
        suppress_source_costs
        or cast_without_mana
        or force_without_mana_cost
        or card.zone != "graveyard"
    ):
        return []
    retrace = compiled_fixed_cast_lifecycle_spec(
        host,
        card,
        FixedCastLifecycleKind.RETRACE,
    )
    if retrace is None:
        return []
    return [
        retrace.retrace_cost_option(option)
        for option in printed
        if str(option.get("id") or "") == "normal"
    ]


__all__ = [
    "FixedCastLifecycleCostHost",
    "retrace_base_options",
    "with_fixed_cast_lifecycle_costs",
]
