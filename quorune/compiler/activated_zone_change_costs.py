from __future__ import annotations

"""Closed fixed single-object zone-change costs for activated abilities."""

from dataclasses import replace

from ..abilities import ActivatedAbility, CostChoice
from ..additional_cost_vocabulary import (
    FIXED_ZONE_CHANGE_COST_CONTRACTS,
    SACRIFICE_ONE_COST,
)
from ..replacement.immutable import FrozenMap
from .spell_additional_cost_templates import (
    fixed_sacrifice_additional_cost_template,
    fixed_zone_change_additional_cost_template,
)


def _zone_change_descriptor(cost_text: str) -> dict[str, object] | None:
    clause = (
        "As an additional cost to cast this spell, "
        + cost_text.strip().removesuffix(".")
        + "."
    )
    template = fixed_zone_change_additional_cost_template(clause)
    if template is not None:
        return dict(template.descriptor)
    sacrifice = fixed_sacrifice_additional_cost_template(clause)
    if sacrifice is None:
        return None
    return {
        "operation": SACRIFICE_ONE_COST,
        "predicate": dict(sacrifice.descriptor["predicate"]),
    }


def fixed_activated_zone_change_cost(
    ability: ActivatedAbility,
) -> ActivatedAbility:
    """Lower one independently closed selected-object cost on an activation."""

    if (
        ability.complex_symbols
        or ability.choices
        or len(ability.uncompiled_costs) != 1
        or ability.discard_source
        or ability.sacrifice_source
        or ability.exile_source
    ):
        return ability
    descriptor = _zone_change_descriptor(ability.uncompiled_costs[0])
    if descriptor is None:
        return ability
    operation = str(descriptor["operation"])
    contract = FIXED_ZONE_CHANGE_COST_CONTRACTS.get(operation)
    predicate = descriptor.get("predicate")
    if contract is None or not isinstance(predicate, dict):
        return ability
    return replace(
        ability,
        choices=(
            CostChoice(
                kind=operation,
                zone=contract[0],
                predicate=FrozenMap(predicate),
            ),
        ),
        uncompiled_costs=(),
    )


__all__ = ["fixed_activated_zone_change_cost"]
