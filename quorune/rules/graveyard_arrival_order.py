"""Owner order over the physical cards entering each graveyard together."""

from collections.abc import Mapping, Sequence

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from ..graveyard_arrival_order_model import GraveyardArrivalGroup, GraveyardArrivalOrders


GRAVEYARD_ARRIVAL_ORDER_CAPABILITY = "zone.graveyard.ordered_simultaneous_arrival"


def graveyard_arrival_groups(prepared, cards: Mapping, apnap_order: Sequence[str]):
    """Use actual replacement destinations and retain each physical origin."""
    seats = tuple(apnap_order)
    if len(seats) != len(set(seats)):
        raise ValueError("Graveyard ordering requires distinct active owners")
    arrivals = {}
    for oid, move in prepared.items():
        card = cards.get(oid)
        if card is None or card.object_id != move.object_id or card.logical_object_id != move.logical_object_id:
            raise ValueError("Prepared graveyard arrival changed its original incarnation")
        if move.origin != "graveyard" and move.destination == "graveyard" and card.is_card_object:
            if card.owner not in seats:
                raise ValueError("Graveyard ordering requires a surviving owner")
            arrivals.setdefault(card.owner, []).append((card.ref, ContinuousObjectIdentity(oid, move.logical_object_id)))
    return tuple(GraveyardArrivalGroup(seat, tuple(arrivals[seat])) for seat in seats
                 if len(arrivals.get(seat, ())) > 1)


def place_ordered_graveyard_arrivals(current_ids: Sequence[str], group: GraveyardArrivalGroup, ordered_refs: Sequence[str]):
    """Keep unrelated positions and apply the owner's bottom-to-top choice."""
    order = group.ordered_ids(ordered_refs)
    wanted = set(order)
    slots = [index for index, oid in enumerate(current_ids) if oid in wanted]
    if len(slots) != len(order) or {current_ids[index] for index in slots} != wanted:
        raise ValueError("The ordered graveyard arrivals changed during commitment")
    positions = list(current_ids)
    for slot, oid in zip(slots, order, strict=True):
        positions[slot] = oid
    return tuple(positions)

class GraveyardArrivalChoiceRequired(Exception):
    """A prepared physical batch must collect its graveyard owner choices."""

    def __init__(self, orders: GraveyardArrivalOrders):
        if orders.current_group is None:
            raise ValueError("A pending graveyard order requires another owner")
        super().__init__("A graveyard owner must order the current arrivals")
        self.orders = orders



def validate_prepared_graveyard_orders(orders, *, prepared, cards, apnap_order):
    groups = graveyard_arrival_groups(prepared, cards, apnap_order)
    if orders is None:
        if groups:
            raise GraveyardArrivalChoiceRequired(GraveyardArrivalOrders(groups))
    elif (not isinstance(orders, GraveyardArrivalOrders) or orders.current_group is not None
            or orders.groups != groups):
        raise GameRuleError("Graveyard orders do not match the actual simultaneous arrivals")
