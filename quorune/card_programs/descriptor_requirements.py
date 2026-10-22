from __future__ import annotations

from typing import Any, Mapping

def typed_result_amount_runtime_dependencies(value: Any) -> tuple[str, ...]:
    """Decode persisted public/scalar amounts without trusting annotations."""
    from ..query_effect_amount_model import (PUBLIC_QUERY_AMOUNT_KIND, PUBLIC_QUERY_AMOUNT_CAPABILITY,
        DECLARED_AMOUNT_CAPABILITY, PublicQueryAmountSpec)
    if isinstance(value, Mapping):
        from ..static_player_restriction_model import static_player_restriction_capabilities
        own = static_player_restriction_capabilities(value) if 'player_restriction' in value else ()
        from ..conditional_recipient_model import conditional_recipient_capabilities
        own = (*own, *conditional_recipient_capabilities(value))
        from ..public_zone_condition_model import public_zone_condition_capabilities
        own = (*own, *public_zone_condition_capabilities(value))
        if value.get('kind') == 'public_quantity_mana':
            from ..public_quantity_mana_model import PublicQuantityManaOutput, PUBLIC_QUANTITY_MANA_CAPABILITY
            PublicQuantityManaOutput.from_dict(value)
            return (PUBLIC_QUANTITY_MANA_CAPABILITY,)
        if value.get('kind') == PUBLIC_QUERY_AMOUNT_KIND:
            spec = PublicQueryAmountSpec.from_dict(value)
            return tuple(sorted({PUBLIC_QUERY_AMOUNT_CAPABILITY,
                                 *((DECLARED_AMOUNT_CAPABILITY,) if spec.schema_version == 2 else ())}))
        if value.get('kind') == 'scalar_effect_amount':
            from ..scalar_effect_amount_model import ScalarEffectAmountSpec, SCALAR_AMOUNT_CAPABILITY
            spec = ScalarEffectAmountSpec.from_dict(value)
            return tuple(sorted({SCALAR_AMOUNT_CAPABILITY,
                                 *((DECLARED_AMOUNT_CAPABILITY,) if spec.binding_id is not None else ())}))
        return tuple(sorted({*own, *(cap for child in value.values() for cap in typed_result_amount_runtime_dependencies(child))}))
    if isinstance(value, (list, tuple)):
        return tuple(sorted({cap for child in value for cap in typed_result_amount_runtime_dependencies(child)}))
    return ()
