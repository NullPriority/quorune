from __future__ import annotations

"""Closed values permitted by already-bound event-player result layouts."""

from .fractional_quantity_model import HalfQuantitySpec, HALF_LIFE_CAPABILITY


def event_result_value_capabilities(operation, field, value):
    if type(value) is int:
        return () if value > 0 else None
    from collections.abc import Mapping
    if isinstance(value,Mapping) and value.get('kind')=='scalar_effect_amount':
        from .scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin,SCALAR_AMOUNT_CAPABILITY
        from .cast_event_amounts import CAST_MANA_VALUE_CAPABILITY
        try:spec=ScalarEffectAmountSpec.from_dict(value)
        except (TypeError,ValueError):return None
        if (spec.origin is not ScalarAmountOrigin.CAST_SPELL_MANA_VALUE or spec.coefficient not in {1,2}
                or (operation,field) not in {('draw','count'),('life','delta'),('lose_life','amount'),('damage','amount')}):return None
        return (SCALAR_AMOUNT_CAPABILITY,CAST_MANA_VALUE_CAPABILITY)
    if operation != 'lose_life' or field != 'amount':
        return None
    try:
        HalfQuantitySpec.from_dict(value)
    except (TypeError, ValueError):
        return None
    return (HALF_LIFE_CAPABILITY,)
