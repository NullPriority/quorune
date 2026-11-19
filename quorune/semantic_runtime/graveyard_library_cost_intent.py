from __future__ import annotations

"""One whole cost containing ordered fixed-size public graveyard units."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..library_order_model import LibraryArrivalOrder
from ..replacement.immutable import FrozenMap, thaw_value
from ..zone_trigger_events import ZoneTransitionKind
from .intents import MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True, slots=True)
class GraveyardLibraryCostBatchIntent(MoveObjectsSimultaneouslyIntent):
    orders: tuple[LibraryArrivalOrder, ...] = ()
    incarnations: FrozenMap = field(default_factory=FrozenMap)
    unit_size: int = 2
    cost_batch_version: int = 1

    def __post_init__(self):
        super(GraveyardLibraryCostBatchIntent, self).__post_init__()
        if not isinstance(self.incarnations, FrozenMap):
            object.__setattr__(self, 'incarnations', FrozenMap(self.incarnations))
        if (type(self.cost_batch_version) is not int or self.cost_batch_version != 1
                or type(self.unit_size) is not int or self.unit_size <= 0
                or not isinstance(self.orders, tuple) or not self.orders
                or any(type(order) is not LibraryArrivalOrder or order.position != 'bottom'
                       or len(order.members) != self.unit_size for order in self.orders)
                or self.object_refs != tuple(ref for order in self.orders for ref in order.members)
                or set(self.incarnations) != set(self.object_refs)
                or any(type(value) is not str or not value for value in self.incarnations.values())):
            raise ValueError('A graveyard cost batch requires each ordered unit and original incarnation')
        if (self.expected_zones != ('graveyard',) or self.destination != 'library'
                or self.owned_only or self.controlled_only or self.library_order is not None
                or self.battlefield_controller is not None or self.battlefield_tapped
                or self.transition_kind is not ZoneTransitionKind.ORDINARY):
            raise ValueError('A graveyard library cost cannot change its canonical movement scope')
        if any(not isinstance(value, Mapping) or type(value.get('event_id')) is not str
               or not value['event_id'] for value in self.replacement_selections):
            raise ValueError('Grouped-cost replacement selections require exact event identities')

    def identity(self):
        return {'actor': self.actor, 'reason': self.reason, 'unit_size': self.unit_size,
                'cost_batch_version': self.cost_batch_version,
                'orders': [order.to_dict() for order in self.orders],
                'incarnations': thaw_value(self.incarnations)}

    @classmethod
    def from_identity(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {
                'actor', 'reason', 'unit_size', 'cost_batch_version', 'orders', 'incarnations'}
                or not isinstance(value['orders'], (list, tuple))):
            raise ValueError('Grouped graveyard cost identity fields are closed')
        orders = tuple(LibraryArrivalOrder.from_dict(order) for order in value['orders'])
        return cls(actor=value['actor'], reason=value['reason'], expected_zones=('graveyard',),
            destination='library', object_refs=tuple(ref for order in orders for ref in order.members),
            orders=orders, incarnations=FrozenMap(value['incarnations']),
            unit_size=value['unit_size'], cost_batch_version=value['cost_batch_version'])
