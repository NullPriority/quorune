"""Closed public observations for the current distinct-land-name threshold."""

from ..characteristic_fragments import (
    CharacteristicQuantityReduction, CharacteristicQuantityScope, CharacteristicQuantitySpec, DISTINCT_LAND_NAMES_QUERY,
)
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, FixedPublicStateConditionKind, FixedPublicStateConditionSpec,
)


def source_or_controlled_land_entry() -> dict:
    return {"any": [
        {"field": "card", "op": "eq", "value": "$source.ref"},
        {"all": [
            {"field": "controller", "op": "eq", "value": "$source.controller"},
            {"field": "types", "op": "contains_any", "value": ["land"]},
        ]},
    ]}


def controlled_distinct_lands_at_least(amount: int) -> FixedPublicStateConditionSpec:
    return FixedPublicStateConditionSpec(
        kind=FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST, amount=amount, schema_version=2,
        quantity=CharacteristicQuantitySpec(
            CharacteristicQuantityScope.CONTROLLER_ZONE, query=DISTINCT_LAND_NAMES_QUERY,
            schema_version=2, reduction=CharacteristicQuantityReduction.DISTINCT_PUBLIC_NAMES,
        ),
    )


def distinct_land_entry_condition(amount: int) -> dict:
    return {"all": [source_or_controlled_land_entry(), {
        "field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, "op": "truthy", "value": True,
        "condition": controlled_distinct_lands_at_least(amount).to_dict(),
    }]}
