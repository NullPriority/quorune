"""Typed inputs for an ordered batch excluding exact object incarnations."""

from collections.abc import Mapping
from dataclasses import dataclass

from .graveyard_arrival_order_model import GraveyardArrivalOrders


@dataclass(frozen=True, slots=True)
class OrderedIncarnationExclusion:
    protected: tuple[tuple[str, str], ...]
    orders: GraveyardArrivalOrders | None = None

    def __post_init__(self):
        protected = tuple(tuple(pair) for pair in self.protected)
        if (any(len(pair) != 2 or any(type(value) is not str or not value for value in pair)
                for pair in protected) or tuple(sorted(set(protected))) != protected):
            raise ValueError("Protected incarnations must be unique canonical object pairs")
        if self.orders is not None and not isinstance(self.orders, GraveyardArrivalOrders):
            raise ValueError("Ordered destruction requires typed graveyard orders")
        object.__setattr__(self, "protected", protected)

    def to_dict(self):
        result = {"schema_version": 1, "protected": [list(pair) for pair in self.protected]}
        if self.orders is not None:
            result["orders"] = self.orders.to_dict()
        return result

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) - {"schema_version", "protected", "orders"}
                or type(value.get("schema_version")) is not int or value["schema_version"] != 1
                or not isinstance(value.get("protected"), (list, tuple))
                or any(not isinstance(pair, (list, tuple)) for pair in value["protected"])):
            raise ValueError("Malformed protected destruction input")
        orders = GraveyardArrivalOrders.from_dict(value["orders"]) if "orders" in value else None
        return cls(value["protected"], orders)
