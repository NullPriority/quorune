from __future__ import annotations

"""Public fixed-generic cost-increase application."""

from typing import Any

from ...compiled_cast_costs import compiled_self_spell_cost_reduction_specs
from ...semantic_runtime.cast_costs import active_fixed_spell_cost_reductions
from ...self_cast_reductions import evaluated_self_reduction


def fixed_generic_reduction(
    host: Any,
    seat: str,
    card: Any,
    *,
    cast_type_line: str | None = None,
) -> int:
    return sum(
        modifier.generic_reduction
        for modifier in active_fixed_spell_cost_reductions(
            host,
            seat,
            card,
            cast_type_line=cast_type_line,
        )
    )


def apply_fixed_generic_increases(
    host: Any,
    seat: str,
    card: Any,
    option: dict[str, Any],
    *,
    cast_type_line: str | None = None,
) -> None:
    increase = sum(
        max(0, modifier.generic_adjustment)
        for modifier in active_fixed_spell_cost_reductions(
            host,
            seat,
            card,
            cast_type_line=cast_type_line,
        )
    )
    if not increase:
        return
    option["requirements"]["GENERIC"] += increase
    option.setdefault("cost_increases", []).append(
        {
            "kind": "fixed_query",
            "count": increase,
        }
    )


def apply_static_reductions(
    host: Any,
    seat: str,
    card: Any,
    option: dict[str, Any],
    *,
    program: Any,
    fixed_reduction: int,
    cast_type_line: str | None = None,
) -> None:
    apply_fixed_generic_increases(
        host,
        seat,
        card,
        option,
        cast_type_line=cast_type_line,
    )
    if fixed_reduction:
        applied = min(
            int(option["requirements"]["GENERIC"]),
            fixed_reduction,
        )
        option["requirements"]["GENERIC"] -= applied
        option.setdefault("cost_reductions", []).append(
            {"kind": "fixed_query", "count": applied}
        )
    self_reduction: dict[str, int] = {}
    for specification in compiled_self_spell_cost_reduction_specs(
        host,
        card.oracle_id,
        spell_program=program,
    ):
        for key, amount in evaluated_self_reduction(
            host,
            seat,
            specification,
        ).items():
            self_reduction[key] = self_reduction.get(key, 0) + amount
    applied_self: dict[str, int] = {}
    for key in sorted(self_reduction):
        applied = min(
            int(option["requirements"].get(key, 0)),
            self_reduction[key],
        )
        if not applied:
            continue
        option["requirements"][key] -= applied
        applied_self[key] = applied
    if applied_self:
        option.setdefault("cost_reductions", []).append(
            {"kind": "self_public", "reduction": applied_self}
        )


__all__ = [
    "apply_fixed_generic_increases",
    "apply_static_reductions",
    "fixed_generic_reduction",
]
