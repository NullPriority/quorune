"""Typed physical return and the locked damage source it settles."""
from dataclasses import dataclass, field
from ..replacement.immutable import FrozenMap, thaw_value
from ..graveyard_arrival_order_model import GraveyardArrivalOrders
from ..damage_source import DamageSourceSnapshot
from .zone_move_intent import MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True, slots=True)
class LinkedLandfallSettlementIntent(MoveObjectsSimultaneouslyIntent):
    damage_source: FrozenMap = field(default_factory=FrozenMap)
    orders: GraveyardArrivalOrders | None = None
    spawn_receipt: FrozenMap | None = None

    def __post_init__(self):
        MoveObjectsSimultaneouslyIntent.__post_init__(self)
        bound=FrozenMap(self.damage_source)
        if (self.expected_zones!=("exile",) or self.destination!="graveyard"
                or self.owned_only or self.controlled_only or self.library_order is not None
                or self.battlefield_controller is not None or self.entry_life_payment_refs
                or (set(bound)!={"source","source_snapshot"} if self.spawn_receipt is None else bool(bound))
                or (self.orders is not None and (not isinstance(self.orders,GraveyardArrivalOrders)
                                                 or self.orders.current_group is not None))):
            raise ValueError("Linked landfall settlement requires its exact exile batch")
        if self.spawn_receipt is not None:
            from ..rules.impulse_spawn_settlement import validate_spawn_receipt

            receipt = validate_spawn_receipt(self.spawn_receipt)
            if receipt["controller"] != self.actor:
                raise ValueError("Spawn settlement changed its original controller")
            object.__setattr__(self, "spawn_receipt", receipt)
            return
        source=DamageSourceSnapshot.from_dict(thaw_value(bound["source_snapshot"]))
        if source.ref!=bound["source"] or not source.object_id or not source.logical_object_id:
            raise ValueError("Linked settlement requires its original damage source snapshot")
        object.__setattr__(self,"damage_source",bound)
