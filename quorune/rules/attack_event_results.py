"""Closed characteristic results on the sealed attacking object."""

from collections.abc import Mapping

from ..qualified_attack_model import ATTACK_CHARACTERISTIC_RESULT_CAPABILITY
from ..query_effect_amount_model import (
    PublicQueryAmountSpec, PUBLIC_QUERY_AMOUNT_CAPABILITY, ATTACK_SHARED_TYPE_AMOUNT_VERSION,
    public_query_amount_runtime_dependencies,
)
from ..characteristic_fragments import CharacteristicQuantityScope,CharacteristicQuantitySpec
from .node_capability_shapes import fixed_source_characteristics_node_capabilities
from .fixed_stat_multipliers import fixed_stat_multiplier_node_capabilities, is_fixed_stat_multiplier


def is_exact_attack_shared_type_power(effect, field):
    return (
        field == "power"
        and effect.get("op") == "apply_source_characteristics_until_end_of_turn"
        and effect.get("card") == "$event.permanent_object"
        and set(effect) == {"op", "card", "power", "toughness", "keywords", "set_card_types",
                            "set_subtypes", "set_colors", "base_power", "base_toughness"}
        and type(effect.get("toughness")) is int and effect["toughness"] == 0
        and effect.get("keywords") in ([], ())
        and all(effect.get(key) is None for key in (
            "set_card_types", "set_subtypes", "set_colors", "base_power", "base_toughness",
        ))
    )


def attack_event_characteristic_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    if effect.get('op')!='apply_source_characteristics_until_end_of_turn' or effect.get('card')!='$event.permanent_object':return ()
    if is_fixed_stat_multiplier(effect):
        return fixed_stat_multiplier_node_capabilities(effects=effects, target_schema=None, mechanic_ids=mechanic_ids)
    projected={**effect,'card':'$source.zone_object'};amounts=set()
    for field in ('power','toughness'):
        value=projected.get(field)
        if isinstance(value,Mapping):
            try:spec=PublicQueryAmountSpec.from_dict(value)
            except (ValueError,TypeError):return ()
            if spec.schema_version == ATTACK_SHARED_TYPE_AMOUNT_VERSION:
                if not is_exact_attack_shared_type_power(effect, field):
                    return ()
                projected[field] = 1
                amounts.update(public_query_amount_runtime_dependencies(value))
                continue
            if (not isinstance(spec.quantity,CharacteristicQuantitySpec)
                    or spec.quantity.scope not in {CharacteristicQuantityScope.CONTROLLER_ZONE,
                    CharacteristicQuantityScope.OPPONENT_ZONES,CharacteristicQuantityScope.ALL_ZONES}
                    or spec.quantity.exclude_source and spec.schema_version != 3
                    or spec.quantity.exclude_attached_object):return ()
            projected[field]=spec.coefficient*2;amounts.add(PUBLIC_QUERY_AMOUNT_CAPABILITY)
    base=fixed_source_characteristics_node_capabilities(effects=(projected,),target_schema=None,mechanic_ids=mechanic_ids)
    return tuple(sorted({ATTACK_CHARACTERISTIC_RESULT_CAPABILITY,'resolution.event_object.fixed_result',*base,*amounts})) if base else ()
