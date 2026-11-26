from __future__ import annotations

"""Require complete mode-price correlation before admitting a Spree wrapper."""

from collections.abc import Mapping

from ..spree_model import SPREE_FIXED_CAPABILITY, SPREE_FIXED_MECHANIC, SpreeModeCosts


def modal_cost_capabilities(*, effects, target_schema, mechanics, cost_schema, modal_resolver):
    from ..entwine_model import ENTWINE_FIXED_MECHANIC
    from ..kicked_modal import KICKED_MODAL_FIELD

    if isinstance(cost_schema, Mapping) and KICKED_MODAL_FIELD in cost_schema:
        if {ENTWINE_FIXED_MECHANIC, SPREE_FIXED_MECHANIC}.intersection(mechanics):
            return ()
        from .kicked_modal_capabilities import kicked_modal_capabilities
        return kicked_modal_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics,
                                       cost_schema=cost_schema, modal_resolver=modal_resolver)
    from ..escalate_tap_model import ESCALATE_TAP_FIELD

    if isinstance(cost_schema, Mapping) and ESCALATE_TAP_FIELD in cost_schema:
        if {ENTWINE_FIXED_MECHANIC, SPREE_FIXED_MECHANIC}.intersection(mechanics):
            return ()
        from .escalate_tap_capabilities import escalate_tap_capabilities

        return escalate_tap_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics,
                                        cost_schema=cost_schema, modal_resolver=modal_resolver)

    from ..pawprint_modes import PAWPRINT_FIELD, PAWPRINT_MECHANIC

    if PAWPRINT_MECHANIC in mechanics or isinstance(cost_schema, Mapping) and PAWPRINT_FIELD in cost_schema:
        if {ENTWINE_FIXED_MECHANIC, SPREE_FIXED_MECHANIC}.intersection(mechanics):
            return ()
        from .pawprint_capabilities import pawprint_capabilities

        return pawprint_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics,
                                     cost_schema=cost_schema, modal_resolver=modal_resolver)
    if ENTWINE_FIXED_MECHANIC in mechanics or isinstance(cost_schema,Mapping) and 'entwine' in cost_schema:
        if SPREE_FIXED_MECHANIC in mechanics or isinstance(cost_schema,Mapping) and 'spree' in cost_schema:return ()
        from .entwine_capability_shapes import entwine_modal_cost_capabilities
        return entwine_modal_cost_capabilities(effects=effects,target_schema=target_schema,
            mechanics=mechanics,cost_schema=cost_schema,modal_resolver=modal_resolver)
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
