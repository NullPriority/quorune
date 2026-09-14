from __future__ import annotations

"""Closed structural vocabulary shared by cost compilation and execution."""


SACRIFICE_COST_KIND = "sacrifice"
ZONE_CHANGE_COST_KIND = "zone_change"
ALTERNATIVE_ADDITIONAL_COST_KIND = "alternative_additional_cost"
FIXED_LIFE_PAYMENT_COST_KIND = "fixed_life_payment"
FIXED_MANA_PAYMENT_COST_KIND = "fixed_mana_payment"

DISCARD_ONE_COST = "discard_one"
SACRIFICE_ONE_COST = "sacrifice_one"
EXILE_ONE_FROM_GRAVEYARD_COST = "exile_one_from_graveyard"
EXILE_ONE_FROM_BATTLEFIELD_COST = "exile_one_from_battlefield"
EXILE_ONE_FROM_HAND_COST = "exile_one_from_hand"
RETURN_ONE_TO_OWNER_HAND_COST = "return_one_to_owner_hand"

FIXED_ZONE_CHANGE_COST_OPERATIONS = frozenset(
    {
        DISCARD_ONE_COST,
        SACRIFICE_ONE_COST,
        EXILE_ONE_FROM_GRAVEYARD_COST,
        EXILE_ONE_FROM_BATTLEFIELD_COST,
        EXILE_ONE_FROM_HAND_COST,
        RETURN_ONE_TO_OWNER_HAND_COST,
    }
)
FIXED_ZONE_CHANGE_COST_CONTRACTS = {
    DISCARD_ONE_COST: ("hand", "graveyard", "discard_cards"),
    SACRIFICE_ONE_COST: (
        "battlefield",
        "graveyard",
        "sacrifice_cards",
    ),
    EXILE_ONE_FROM_GRAVEYARD_COST: (
        "graveyard",
        "exile",
        "exile_cards",
    ),
    EXILE_ONE_FROM_BATTLEFIELD_COST: (
        "battlefield",
        "exile",
        "exile_cards",
    ),
    EXILE_ONE_FROM_HAND_COST: ("hand", "exile", "exile_cards"),
    RETURN_ONE_TO_OWNER_HAND_COST: (
        "battlefield",
        "hand",
        "return_cards",
    ),
}


__all__ = [
    "ALTERNATIVE_ADDITIONAL_COST_KIND",
    "DISCARD_ONE_COST",
    "EXILE_ONE_FROM_BATTLEFIELD_COST",
    "EXILE_ONE_FROM_GRAVEYARD_COST",
    "EXILE_ONE_FROM_HAND_COST",
    "FIXED_ZONE_CHANGE_COST_OPERATIONS",
    "FIXED_ZONE_CHANGE_COST_CONTRACTS",
    "FIXED_LIFE_PAYMENT_COST_KIND",
    "FIXED_MANA_PAYMENT_COST_KIND",
    "RETURN_ONE_TO_OWNER_HAND_COST",
    "SACRIFICE_COST_KIND",
    "SACRIFICE_ONE_COST",
    "ZONE_CHANGE_COST_KIND",
]
