from __future__ import annotations

"""One closed simultaneous discard price with a required Island among two cards."""

from ..additional_cost_vocabulary import DISCARD_ONE_COST
from ..object_predicate import ObjectQuerySpec


ISLAND_PAIR_DISCARD_VERSION = 11
ISLAND_PAIR_DISCARD_FIELD = "island_pair"
ISLAND_PAIR_DISCARD_CAPABILITY = "casting.alternative_cost.fixed_island_pair_discard"
ISLAND_PAIR_PRICE_TEXT = "discard an island card and another card"


def _hand_predicate(**qualification):
    return ObjectQuerySpec(zones=("hand",), owner="$actor", known_to_actor=True, **qualification)


def validate_island_pair_discard_cost(cost):
    if cost.schema_version != ISLAND_PAIR_DISCARD_VERSION:
        if cost.island_pair is not False:
            raise ValueError("An Island discard pair requires its exact version")
        return
    if (cost.island_pair is not True or cost.operation != DISCARD_ONE_COST or cost.count != 2
            or cost.unblocked_attacker is not False or cost.predicate != _hand_predicate()):
        raise ValueError("An Island discard pair requires exactly two cards from the actor's hand")


def island_pair_discard_cost():
    from .casting_additional_costs import FixedZoneChangeAdditionalCost

    return FixedZoneChangeAdditionalCost(
        operation=DISCARD_ONE_COST, choice_field="discard_cards", predicate=_hand_predicate(),
        count=2, schema_version=ISLAND_PAIR_DISCARD_VERSION, island_pair=True,
    )


def selected_discard_has_island(host, actor, refs, *, exclude_object_id=None):
    """Use the ordinary live Island-subtype query for a submitted pair."""
    from .casting_additional_costs import FixedZoneChangeAdditionalCost, fixed_zone_change_cost_candidates

    island = FixedZoneChangeAdditionalCost(
        operation=DISCARD_ONE_COST, choice_field="discard_cards",
        predicate=_hand_predicate(subtypes_all=("island",)),
    )
    return any(ref in refs for ref in fixed_zone_change_cost_candidates(
        host, actor=actor, cost=island, exclude_object_id=exclude_object_id,
    ))
