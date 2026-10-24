from __future__ import annotations

"""Closed version-five zone quantity validation and producer authority."""

from typing import Any,Mapping
from .object_predicate import ObjectQuerySpec

PUBLIC_ZONE_COLLECTION_CAPABILITY='quantity_expression.public_owned_zone_collection'
ZONE_CARDINALITY_CAPABILITY='quantity_expression.zone_cardinality'


def validate_public_zone_quantity(quantity):
    from .characteristic_fragments import CharacteristicQuantityScope as S,CharacteristicQuantityReduction as R
    query=quantity.query
    if (quantity.schema_version!=5 or quantity.scope not in {S.CONTROLLER_ZONE,S.OPPONENT_ZONES,S.ALL_ZONES}
            or not isinstance(query,ObjectQuerySpec) or quantity.counter_name is not None or quantity.exclude_source
            or quantity.exclude_attached_object or quantity.terms or quantity.mana_colors):
        raise ValueError('Version-five zone quantity requires a closed card collection')
    if query.zones in {('hand',),('library',)}:
        if query!=ObjectQuerySpec(zones=query.zones) or quantity.reduction not in {R.OBJECT_COUNT,R.MAXIMUM_ZONE_SIZE}:
            raise ValueError('Hidden zone quantities expose only raw card cardinality')
        return
    if (query.zones not in {('graveyard',),('exile',),('exile','graveyard')}
            or quantity.reduction is not R.OBJECT_COUNT
            or query!=ObjectQuerySpec(zones=query.zones,token=False,types_all=query.types_all,types_any=query.types_any,excluded_types=query.excluded_types)
            or not set((*query.types_all,*query.types_any,*query.excluded_types))<={'artifact','battle','creature','enchantment','instant','kindred','land','planeswalker','sorcery'}):
        raise ValueError('Public zone collection requires explicit owned face-up card qualities')


def public_zone_quantity_dependencies(value:Any)->tuple[str,...]:
    if not isinstance(value,Mapping):return ()
    from .query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND
    if value.get('kind')==PUBLIC_QUERY_AMOUNT_KIND:return public_zone_quantity_dependencies(value.get('quantity'))
    if value.get('schema_version')!=5 or not {'scope','query','counter_name','exclude_source'}<=set(value):return ()
    from .characteristic_fragments import CharacteristicQuantitySpec
    quantity=CharacteristicQuantitySpec.from_dict(value)
    return (ZONE_CARDINALITY_CAPABILITY if quantity.query.zones in {('hand',),('library',)} else PUBLIC_ZONE_COLLECTION_CAPABILITY,)
