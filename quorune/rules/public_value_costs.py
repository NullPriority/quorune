from __future__ import annotations

"""Descriptor-owned authority for current provider power in cost metrics."""

from collections.abc import Mapping

PUBLIC_SOURCE_POWER_COST_CAPABILITY = 'cost.modification.public_source_power'


def public_value_cost_dependencies(value):
    if isinstance(value, Mapping):
        own = set()
        if value.get('handler_id') == 'modification.activation-cost.public-quantity.v3':
            from ..semantic_runtime.activation_costs import PublicQuantityActivationCostModifierHandler
            spec = PublicQuantityActivationCostModifierHandler().validate(value)
            own.add('activation.mana_cost.public_quantity')
            if spec.ability_scope == 'boast':
                own.add('activation.boast.fixed_restrictions')
        if value.get('kind') == 'source_power':
            from ..self_cast_reductions import CastReductionMetric
            CastReductionMetric.from_dict(value)
            own.add(PUBLIC_SOURCE_POWER_COST_CAPABILITY)
        return tuple(sorted(own | {cap for child in value.values() for cap in public_value_cost_dependencies(child)}))
    if isinstance(value, (list, tuple)):
        return tuple(sorted({cap for child in value for cap in public_value_cost_dependencies(child)}))
    return ()
