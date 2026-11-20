from __future__ import annotations

"""Closed fixed current-hand putback, distinct from drawing or hand redraw."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from .library_order_model import LibraryArrivalOrder, DepartedLibraryArrivalOrder

FIXED_HAND_PUTBACK_OPERATION = 'fixed_hand_putback'
FIXED_HAND_PUTBACK_CAPABILITY = 'library.order.fixed_hand_putback'
FIXED_HAND_PUTBACK_MECHANIC = 'fixed-draw-hand-putback'


class PutbackPlacement(StrEnum):
    BOTTOM = 'bottom'
    SHUFFLE = 'shuffle'
    EITHER = 'either'


@dataclass(frozen=True, slots=True)
class FixedHandPutbackSpec:
    count: int
    placement: PutbackPlacement
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.count) is not int or not isinstance(self.placement, PutbackPlacement)
                or (self.count, self.placement.value) not in {(1,'bottom'),(2,'bottom'),(1,'shuffle'),(2,'either')}):
            raise ValueError('Fixed putback requires one represented version1 count and placement')

    def to_dict(self):
        return {'schema_version':1,'count':self.count,'placement':self.placement.value}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','count','placement'}:
            raise ValueError('Fixed putback fields are incomplete or unknown')
        return cls(value['count'],PutbackPlacement(value['placement']),value['schema_version'])

    def effect(self):
        return {'op':FIXED_HAND_PUTBACK_OPERATION,'player':'$controller','putback':self.to_dict()}

    def required(self,available):
        if type(available) is not int or available<0:
            raise ValueError('Available hand cardinality must be nonnegative')
        return min(self.count,available)

    def resolved_placement(self,chosen=None):
        if self.placement is PutbackPlacement.EITHER:
            if chosen not in ('top','bottom'):
                raise ValueError('Choose one library edge for the entire putback group')
            return chosen
        if chosen is not None:
            raise ValueError('Fixed placement cannot accept an alternative edge')
        return self.placement.value


def decode_fixed_hand_putback(effect,*,player=None):
    if (not isinstance(effect,Mapping) or set(effect)!={'op','player','putback'}
            or effect['op']!=FIXED_HAND_PUTBACK_OPERATION
            or type(effect['player']) is not str or not effect['player']
            or effect['player']!=('$controller' if player is None else player)):
        raise ValueError('Fixed putback requires its closed controller-bound instruction')
    return FixedHandPutbackSpec.from_dict(effect['putback'])


@dataclass(frozen=True, slots=True)
class FixedHandPutbackOrder(DepartedLibraryArrivalOrder):
    """Explicit new private hand-order tag; first card is nearest its edge."""

    def __post_init__(self):
        LibraryArrivalOrder.__post_init__(self)

    @property
    def origin_zones(self):
        return ('hand',)

    def to_dict(self):
        return {**LibraryArrivalOrder.to_dict(self),'scope':'fixed_hand_putback_v1'}
