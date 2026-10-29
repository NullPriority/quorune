"""Closed characteristic results on the sealed attacking object."""

from collections.abc import Mapping

from ..qualified_attack_model import ATTACK_CHARACTERISTIC_RESULT_CAPABILITY
from ..query_effect_amount_model import PublicQueryAmountSpec,PUBLIC_QUERY_AMOUNT_CAPABILITY
from ..characteristic_fragments import CharacteristicQuantityScope,CharacteristicQuantitySpec
from .node_capability_shapes import fixed_source_characteristics_node_capabilities


def attack_event_characteristic_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    if effect.get('op')!='apply_source_characteristics_until_end_of_turn' or effect.get('card')!='$event.permanent_object':return ()
    projected={**effect,'card':'$source.zone_object'};amounts=set()
    for field in ('power','toughness'):
        value=projected.get(field)
        if isinstance(value,Mapping):
            try:spec=PublicQueryAmountSpec.from_dict(value)
            except (ValueError,TypeError):return ()
            if (not isinstance(spec.quantity,CharacteristicQuantitySpec)
                    or spec.quantity.scope not in {CharacteristicQuantityScope.CONTROLLER_ZONE,
                    CharacteristicQuantityScope.OPPONENT_ZONES,CharacteristicQuantityScope.ALL_ZONES}
                    or spec.quantity.exclude_source or spec.quantity.exclude_attached_object):return ()
            projected[field]=spec.coefficient*2;amounts.add(PUBLIC_QUERY_AMOUNT_CAPABILITY)
    base=fixed_source_characteristics_node_capabilities(effects=(projected,),target_schema=None,mechanic_ids=mechanic_ids)
    return tuple(sorted({ATTACK_CHARACTERISTIC_RESULT_CAPABILITY,'resolution.event_object.fixed_result',*base,*amounts})) if base else ()
