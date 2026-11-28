from __future__ import annotations

"""Require complete mode-price correlation before admitting a Spree wrapper."""

from collections.abc import Mapping

from ..spree_model import MODE_MANA_PRICE_FIELDS, MODE_MANA_PRICE_MECHANICS, fixed_mode_mana_costs


def modal_cost_capabilities(*, effects, target_schema, mechanics, cost_schema, modal_resolver):
    from ..entwine_model import ENTWINE_FIXED_MECHANIC
    from ..kicked_modal import KICKED_MODAL_FIELD

    if isinstance(cost_schema, Mapping) and KICKED_MODAL_FIELD in cost_schema:
        if ({ENTWINE_FIXED_MECHANIC} | MODE_MANA_PRICE_MECHANICS).intersection(mechanics):
            return ()
        from .kicked_modal_capabilities import kicked_modal_capabilities
        return kicked_modal_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics,
                                       cost_schema=cost_schema, modal_resolver=modal_resolver)
    from ..escalate_tap_model import ESCALATE_TAP_FIELD
    from ..escalate_mana_model import ESCALATE_MANA_FIELD

    if isinstance(cost_schema, Mapping) and ESCALATE_MANA_FIELD in cost_schema:
        if ({ENTWINE_FIXED_MECHANIC} | MODE_MANA_PRICE_MECHANICS).intersection(mechanics):
            return ()
        from .escalate_tap_capabilities import escalate_mana_capabilities

        return escalate_mana_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics,
                                         cost_schema=cost_schema, modal_resolver=modal_resolver)

    if isinstance(cost_schema, Mapping) and ESCALATE_TAP_FIELD in cost_schema:
        if ({ENTWINE_FIXED_MECHANIC} | MODE_MANA_PRICE_MECHANICS).intersection(mechanics):
            return ()
        from .escalate_tap_capabilities import escalate_tap_capabilities

        return escalate_tap_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics,
                                        cost_schema=cost_schema, modal_resolver=modal_resolver)

    from ..pawprint_modes import PAWPRINT_FIELD, PAWPRINT_MECHANIC, REPEATABLE_MODE_FIELD, REPEATABLE_MODE_MECHANIC

    if ({PAWPRINT_MECHANIC, REPEATABLE_MODE_MECHANIC}.intersection(mechanics)
            or isinstance(cost_schema, Mapping) and {PAWPRINT_FIELD, REPEATABLE_MODE_FIELD}.intersection(cost_schema)):
        if ({ENTWINE_FIXED_MECHANIC} | MODE_MANA_PRICE_MECHANICS).intersection(mechanics):
            return ()
        from .pawprint_capabilities import pawprint_capabilities

        return pawprint_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics,
                                     cost_schema=cost_schema, modal_resolver=modal_resolver)
    if ENTWINE_FIXED_MECHANIC in mechanics or isinstance(cost_schema,Mapping) and 'entwine' in cost_schema:
        if MODE_MANA_PRICE_MECHANICS.intersection(mechanics) or isinstance(cost_schema, Mapping) and MODE_MANA_PRICE_FIELDS.intersection(cost_schema):return ()
        from .entwine_capability_shapes import entwine_modal_cost_capabilities
        return entwine_modal_cost_capabilities(effects=effects,target_schema=target_schema,
            mechanics=mechanics,cost_schema=cost_schema,modal_resolver=modal_resolver)
    tagged = MODE_MANA_PRICE_MECHANICS.intersection(mechanics)
    priced = isinstance(cost_schema, Mapping) and MODE_MANA_PRICE_FIELDS.intersection(cost_schema)
    if not tagged and not priced:
        return modal_resolver(effects=effects, target_schema=target_schema, mechanics=mechanics)
    if len(tagged) != 1 or not priced:
        return ()
    try:
        prices = fixed_mode_mana_costs(cost_schema)
        if set(cost_schema) != {prices.field} or tagged != {prices.mechanic}:
            return ()
        prices.validate_schema(target_schema)
    except (TypeError, ValueError, KeyError):
        return ()
    ordinary = tuple(value for value in mechanics if value != prices.mechanic)
    dependencies = modal_resolver(effects=effects, target_schema=target_schema, mechanics=ordinary)
    return tuple(sorted({*dependencies, prices.capability})) if dependencies else ()
