from __future__ import annotations

"""Closed static characteristic children that cannot grant further components."""

from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
)
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec


def granted_characteristic_handlers_are_closed(handlers):
    if len(handlers) != 1:
        return False
    descriptor = handlers[0]
    if descriptor.get('handler_id') == 'continuous.attached.fixed-characteristics.v1':
        from ..semantic_runtime.attached_continuous import AttachedFixedCharacteristicsHandler
        node = AttachedFixedCharacteristicsHandler().validate(descriptor)
        return (node.subject_types_all == ('creature',)
            and not any((node.type_operations, node.color_operations, node.remove_abilities,
                node.remove_all_abilities, node.add_rules_text, node.add_ability_fragments,
                node.quantity_power, node.quantity_toughness))
            and node.base_power is None and node.base_toughness is None and node.quantity is None)
    if descriptor.get('handler_id') == 'continuous.anthem.fixed-query.v2':
        from ..semantic_runtime.continuous_components import FixedQueryPowerToughnessAnthemHandler
        FixedQueryPowerToughnessAnthemHandler().validate(descriptor)
        query = ObjectQuerySpec.from_dict(descriptor['condition']['predicate'])
        public = ObjectQuerySpec(zones=('battlefield',), types_all=query.types_all, types_any=query.types_any,
            excluded_types=query.excluded_types, subtypes_all=query.subtypes_all, subtypes_any=query.subtypes_any,
            supertypes_all=query.supertypes_all, excluded_supertypes=query.excluded_supertypes,
            colors_all=query.colors_all, colors_any=query.colors_any, colorless=query.colorless,
            keywords_all=query.keywords_all, keywords_none=query.keywords_none, token=query.token,
            commander=query.commander)
        return query == public
    if descriptor.get('handler_id') == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID:
        return _fixed_public_state_child_is_closed(descriptor)
    return False


def _fixed_public_state_child_is_closed(descriptor):
    from ..semantic_runtime.conditional_continuous import FixedPublicStateCharacteristicsHandler

    if 'recipient' in descriptor:
        return False
    node = FixedPublicStateCharacteristicsHandler().validate(descriptor)
    if node.target_kind != 'source' or node.fragments:
        return False
    if node.source_condition.kind is FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY:
        return _untapped_source_hexproof_is_closed(node)

    controlled_swamp = FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST,
        amount=1,
        quantity=CharacteristicQuantitySpec(
            CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=('battlefield',), types_all=('land',), subtypes_all=('swamp',)),
        ),
        schema_version=2,
    )
    return (node.source_condition == controlled_swamp and not node.abilities
        and node.power == node.toughness == 1)


def _untapped_source_hexproof_is_closed(node):
    untapped_source = FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
        predicate=ObjectQuerySpec(zones=('battlefield',), state_predicate=PermanentStatePredicateSpec(tapped=False)),
        schema_version=2,
    )
    return (node.source_condition == untapped_source and node.abilities == ('Hexproof',)
        and node.power == node.toughness == 0)


def granted_characteristic_occurrences(fragments, eligible_keys):
    from ..ability_fragments import StaticComponentSpec, canonical_ability_fragments
    result = []
    for fragment in canonical_ability_fragments(fragments):
        if not isinstance(fragment, StaticComponentSpec) or fragment.schema_version != 3 or fragment.semantic_key not in eligible_keys:
            continue
        if fragment.grant_timestamp is None:
            raise ValueError('Current granted characteristic lacks its owning effect timestamp')
        result.append((fragment.semantic_key, fragment.grant_timestamp))
    return tuple(result)
