from __future__ import annotations

"""Closed composition of a fixed life price and one selected zone price."""

from collections.abc import Mapping
from ..additional_cost_vocabulary import DISCARD_ONE_COST, SACRIFICE_ONE_COST, EXILE_ONE_FROM_GRAVEYARD_COST

from .casting_additional_cost_groups import (
    FixedLifePaymentAdditionalCost, fixed_zone_change_additional_cost_capability,
)
from .casting_additional_costs import FixedZoneChangeAdditionalCost
from .tap_additional_cost import COLOR_TAP_SCHEMA_VERSION, TAP_ADDITIONAL_COST_KIND, FixedTapAdditionalCost


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


def fixed_color_tap_zone_costs(values):
    """Recognize a color tap followed by a single independently typed zone price."""
    if (not isinstance(values, (list, tuple)) or len(values) != 2
        or any(not isinstance(value, Mapping) for value in values)):
        return None
    taps = [index for index, value in enumerate(values) if value.get("kind") == TAP_ADDITIONAL_COST_KIND]
    if len(taps) != 1:
        return None
    index = taps[0]
    try:
        tap = FixedTapAdditionalCost.from_descriptor(values[index])
        zone = FixedZoneChangeAdditionalCost.from_descriptor(values[1 - index])
    except (TypeError, ValueError):
        return None
    if tap.schema_version != COLOR_TAP_SCHEMA_VERSION or zone.count_parameter is not None or zone.unblocked_attacker:
        return None
    return tap, zone


FIXED_DISCARD_SACRIFICE_CAPABILITY = "casting.additional_cost.fixed_discard_sacrifice"
FIXED_DISCARD_SACRIFICE_FIELD = "fixed_discard_sacrifice"
FIXED_DISCARD_SACRIFICE_ORDER = "fixed_discard_sacrifice_order"

WHOLE_DISCARD_SACRIFICE_CAPABILITY = "casting.additional_cost.automatic_whole_discard_sacrifice"
WHOLE_DISCARD_SACRIFICE_VERSION = 2


def fixed_discard_sacrifice_costs(values):
    """Decode two fixed prices with distinct origins and input fields."""
    if not isinstance(values, (tuple, list)) or len(values) != 2:
        return ()
    try:
        costs = tuple(FixedZoneChangeAdditionalCost.from_descriptor(value) for value in values)
    except (TypeError, ValueError):
        return ()
    if {cost.operation for cost in costs} != {DISCARD_ONE_COST, SACRIFICE_ONE_COST}:
        return ()
    if any(cost.schema_version != 1 or cost.count != 1 or cost.count_parameter is not None
           or cost.unblocked_attacker for cost in costs):
        return ()
    return costs


def whole_discard_sacrifice_leaves(*, sacrifice_first=True):
    from ..object_predicate import ObjectQuerySpec

    sacrifice = FixedZoneChangeAdditionalCost(
        SACRIFICE_ONE_COST, "sacrifice_cards",
        ObjectQuerySpec(zones=("battlefield",), controller="$actor", known_to_actor=True),
    )
    discard = FixedZoneChangeAdditionalCost(
        DISCARD_ONE_COST, "discard_cards",
        ObjectQuerySpec(zones=("hand",), owner="$actor", known_to_actor=True),
    )
    return (sacrifice, discard) if sacrifice_first else (discard, sacrifice)


def fixed_discard_sacrifice_dependencies(cost_schema):
    """Validate the complete declared pair before deriving its independent authorities."""
    if not isinstance(cost_schema, Mapping) or FIXED_DISCARD_SACRIFICE_FIELD not in cost_schema:
        return ()
    version = cost_schema[FIXED_DISCARD_SACRIFICE_FIELD]
    costs = fixed_discard_sacrifice_costs(cost_schema.get("additional_costs"))
    if set(cost_schema) != {FIXED_DISCARD_SACRIFICE_FIELD, "additional_costs"} or (
        type(version) is not int or version not in (1, WHOLE_DISCARD_SACRIFICE_VERSION) or not costs
    ):
        raise ValueError("The mandatory discard and sacrifice require two exact costs")
    owner = FIXED_DISCARD_SACRIFICE_CAPABILITY
    if version == WHOLE_DISCARD_SACRIFICE_VERSION:
        if costs not in (whole_discard_sacrifice_leaves(), whole_discard_sacrifice_leaves(sacrifice_first=False)):
            raise ValueError("Automatic zone prices must admit the whole controlled battlefield and owned hand")
        owner = WHOLE_DISCARD_SACRIFICE_CAPABILITY
    return (owner, *(fixed_zone_change_additional_cost_capability(cost) for cost in costs))


def fixed_discard_sacrifice_order(option, costs):
    raw = option.get(FIXED_DISCARD_SACRIFICE_ORDER)
    if raw is None:
        return ()
    if not fixed_discard_sacrifice_costs(costs) or not isinstance(raw, (tuple, list)):
        raise ValueError("The mandatory cost order requires its declared discard and sacrifice")
    order = tuple(raw)
    if any(type(position) is not int for position in order) or order not in ((0, 1), (1, 0)):
        raise ValueError("Choose an offered order for the two mandatory costs")
    return order
