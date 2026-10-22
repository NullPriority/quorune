from __future__ import annotations

"""Closed composition of a fixed life price and one selected zone price."""

from collections.abc import Mapping
from ..additional_cost_vocabulary import DISCARD_ONE_COST, SACRIFICE_ONE_COST, EXILE_ONE_FROM_GRAVEYARD_COST

from .casting_additional_cost_groups import (
    FixedLifePaymentAdditionalCost, fixed_zone_change_additional_cost_capability,
)
from .casting_additional_costs import FixedZoneChangeAdditionalCost


FIXED_LIFE_ZONE_CAPABILITY = 'casting.additional_cost.fixed_life_zone'


def decode_fixed_life_zone_costs(values):
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise ValueError('Life and selected-resource payment requires two ordered typed prices')
    if any(not isinstance(value, Mapping) for value in values):
        raise ValueError('Life and selected-resource prices must be typed objects')
    life = FixedLifePaymentAdditionalCost.from_descriptor(values[0])
    zone = FixedZoneChangeAdditionalCost.from_descriptor(values[1])
    if (zone.schema_version != 1 or zone.count_parameter is not None or zone.unblocked_attacker
            or zone.operation not in {DISCARD_ONE_COST, SACRIFICE_ONE_COST, EXILE_ONE_FROM_GRAVEYARD_COST}):
        raise ValueError('Life and selected-resource payment requires a fixed zone price')
    return life, zone


def fixed_life_zone_costs(values):
    try:
        decode_fixed_life_zone_costs(values)
    except (TypeError, ValueError):
        return False
    return True


def fixed_life_zone_dependencies(values):
    _, zone = decode_fixed_life_zone_costs(values)
    return tuple(sorted((FIXED_LIFE_ZONE_CAPABILITY, 'casting.additional_cost.fixed_life_payment',
        fixed_zone_change_additional_cost_capability(zone))))
