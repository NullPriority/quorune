from __future__ import annotations

from typing import Any, Mapping

from .rules.casting import build_cast_cost_options


class CastingCostHostMixin:
    """Narrow engine facade for immutable casting-cost option queries."""

    def _cast_cost_options(
        self,
        seat: str,
        card: Any,
        program: Any,
        *,
        response: Mapping[str, Any] | None = None,
        hint: bool,
        force_without_mana_cost: bool = False,
        alternative_base: Mapping[str, Any] | None = None,
        cast_type_line: str | None = None,
        suppress_source_costs: bool = False,
        resolving_exile_authority: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            option.to_dict()
            for option in build_cast_cost_options(
                self,
                seat,
                card,
                program,
                response=response,
                hint=hint,
                force_without_mana_cost=force_without_mana_cost,
                alternative_base=alternative_base,
                cast_type_line=cast_type_line,
                suppress_source_costs=suppress_source_costs,
                resolving_exile_authority=resolving_exile_authority,
            )
        ]


__all__ = ["CastingCostHostMixin"]
