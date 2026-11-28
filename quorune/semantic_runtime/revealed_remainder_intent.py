"""Typed ordered departure for a finished revealed battlefield selection."""

from dataclasses import dataclass, field

from ..replacement.immutable import FrozenMap
from ..graveyard_arrival_order_model import GraveyardArrivalOrders
from .zone_move_intent import MoveObjectsSimultaneouslyIntent


@dataclass(frozen=True, slots=True)
class RevealedRemainderMoveIntent(MoveObjectsSimultaneouslyIntent):
    graveyard_orders: GraveyardArrivalOrders | None = None

    def __post_init__(self):
        MoveObjectsSimultaneouslyIntent.__post_init__(self)
        if (self.destination != "graveyard" or not self.owned_only or not self.object_refs
                or set(self.expected_zones) - {"library", "exile", "command"}
                or self.graveyard_orders is not None and (
                    not isinstance(self.graveyard_orders, GraveyardArrivalOrders)
                    or self.graveyard_orders.current_group is not None)):
            raise ValueError("A revealed remainder requires an owned graveyard arrival and its full order")


@dataclass(frozen=True, slots=True)
class RevealedBattlefieldMoveIntent(MoveObjectsSimultaneouslyIntent):
    aura_targets: FrozenMap = field(default_factory=FrozenMap)
    battle_protectors: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self):
        MoveObjectsSimultaneouslyIntent.__post_init__(self)
        if self.destination != "battlefield" or self.expected_zones != ("library",) or not self.owned_only:
            raise ValueError("A revealed battlefield batch requires its original owned library members")
        for values in (self.aura_targets, self.battle_protectors):
            if (not isinstance(values, FrozenMap) or set(values) - set(self.object_refs)
                    or any(type(value) is not str or not value for value in values.values())):
                raise ValueError("An entry choice must bind an exact selected card")
