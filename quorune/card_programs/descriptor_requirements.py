from __future__ import annotations

from typing import Any, Mapping

def typed_result_amount_runtime_dependencies(value: Any) -> tuple[str, ...]:
    """Decode persisted public/scalar amounts without trusting annotations."""
    from ..query_effect_amount_model import (PUBLIC_QUERY_AMOUNT_KIND, PUBLIC_QUERY_AMOUNT_CAPABILITY,
        DECLARED_AMOUNT_CAPABILITY, PublicQueryAmountSpec)
    if isinstance(value, Mapping):
        from ..rules.modal_node_capabilities import modal_schema_runtime_dependencies
        modal=modal_schema_runtime_dependencies(value)
        if modal:
            return tuple(sorted({*modal,*(cap for child in value.values() for cap in typed_result_amount_runtime_dependencies(child))}))
        from ..rules.all_kind_counter_shapes import all_kind_counter_payload_dependencies
        all_kinds=all_kind_counter_payload_dependencies(value)
        if all_kinds:
            return tuple(sorted({*all_kinds,*(cap for child in value.values() for cap in typed_result_amount_runtime_dependencies(child))}))
        from ..rules.counter_target_set_shapes import quantified_counter_payload_dependencies
        counter_targets=quantified_counter_payload_dependencies(value)
        if counter_targets:
            return tuple(sorted({*counter_targets,*(cap for child in value.values() for cap in typed_result_amount_runtime_dependencies(child))}))
        from ..target_count_cost_model import TARGET_COUNT_COST_HANDLER, TARGET_COUNT_COST_CAPABILITY, FixedManaPerTargetCostSpec
        if value.get('handler_id') == TARGET_COUNT_COST_HANDLER:
            FixedManaPerTargetCostSpec.from_dict(value['target_cost'])
            return (TARGET_COUNT_COST_CAPABILITY,)
        from ..target_cardinality import SELECTED_TARGET_COUNT_KIND, ANY_NUMBER_TARGET_CAPABILITY, validate_selected_target_count
        if value.get('kind') == SELECTED_TARGET_COUNT_KIND:
            validate_selected_target_count(value)
            return (ANY_NUMBER_TARGET_CAPABILITY,)
        from ..static_player_restriction_model import static_player_restriction_capabilities
        own = static_player_restriction_capabilities(value) if 'player_restriction' in value else ()
        from ..rules.casting_additional_cost_groups import selected_zone_cost_payload_dependencies
        own = (*own, *selected_zone_cost_payload_dependencies(value))
        from ..fixed_effect_payment import compound_payment_payload_dependencies
        own = (*own, *compound_payment_payload_dependencies(value))
        from ..conditional_recipient_model import conditional_recipient_capabilities
        own = (*own, *conditional_recipient_capabilities(value))
        from ..public_zone_condition_model import public_zone_condition_capabilities
        own = (*own, *public_zone_condition_capabilities(value))
        from ..rules.conditional_free_cast_shapes import conditional_free_cast_capabilities
        own = (*own, *conditional_free_cast_capabilities(value))
        from ..land_entry_condition_model import land_entry_condition_capabilities
        own = (*own, *land_entry_condition_capabilities(value))
        from ..nonmana_kicker import NONMANA_KICKER_HANDLER,FixedNonmanaKickerSpec,nonmana_kicker_dependencies
        if value.get('handler_id')==NONMANA_KICKER_HANDLER:
            own=(*own,*nonmana_kicker_dependencies(FixedNonmanaKickerSpec.from_dict(value['kicker'])))
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
