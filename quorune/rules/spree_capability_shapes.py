from __future__ import annotations

"""Require complete mode-price correlation before admitting a Spree wrapper."""

from collections.abc import Mapping

from ..spree_model import SPREE_FIXED_CAPABILITY, SPREE_FIXED_MECHANIC, SpreeModeCosts


def modal_cost_capabilities(*, effects, target_schema, mechanics, cost_schema, modal_resolver):
    tagged = SPREE_FIXED_MECHANIC in mechanics
    priced = isinstance(cost_schema, Mapping) and 'spree' in cost_schema
    if not tagged and not priced:
        return modal_resolver(effects=effects, target_schema=target_schema, mechanics=mechanics)
    if not tagged or not priced or set(cost_schema) != {'spree'}:
        return ()
    try:
        prices = SpreeModeCosts.from_dict(cost_schema['spree'])
        prices.validate_schema(target_schema)
    except (TypeError, ValueError, KeyError):
        return ()
    ordinary = tuple(value for value in mechanics if value != SPREE_FIXED_MECHANIC)
    dependencies = modal_resolver(effects=effects, target_schema=target_schema, mechanics=ordinary)
    return tuple(sorted({*dependencies, SPREE_FIXED_CAPABILITY})) if dependencies else ()
