from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..dynamic_characteristics import query_characteristic_count
from ..query_effect_amount_model import (
    PublicQueryAmountError,
    PublicQueryAmountSpec,
    CastXAmountSpec,
)


class PublicQueryAmountHost(Protocol):
    state: Any

    def _effective_card_data(
        self,
        card: Any,
        *,
        maximum_layer: Any | None = None,
        _enforce_static_component_applicability: bool = True,
    ) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


@dataclass(frozen=True, slots=True)
class _ResolutionQuantitySource:
    controller: str
    ref: str


def resolve_public_query_amount(
    host: PublicQueryAmountHost,
    value: Mapping[str, Any],
    item: Any,
) -> int:
    """Resolve a typed amount for the stack object's locked controller."""

    spec = PublicQueryAmountSpec.from_dict(value)
    controller = getattr(item, "controller", None)
    stack_ref = getattr(item, "ref", None)
    if type(controller) is not str or not controller:
        raise PublicQueryAmountError(
            "Public query effect amount requires a stack controller"
        )
    if type(stack_ref) is not str or not stack_ref:
        raise PublicQueryAmountError(
            "Public query effect amount requires stack identity"
        )
    cache = None
    if spec.binding_id is not None:
        cache = item.context.setdefault("declared_public_amounts", {})
        if not isinstance(cache, dict):
            raise PublicQueryAmountError("Declared amount continuation is malformed")
        if spec.binding_id in cache:
            previous = cache[spec.binding_id]
            if not isinstance(previous, Mapping) or set(previous) != {"controller", "quantity", "value"} or previous["controller"] != controller or previous["quantity"] != spec.quantity.to_dict() or type(previous["value"]) is not int or previous["value"] < 0:
                raise PublicQueryAmountError("Declared amount continuation changed its identity or value")
            return spec.coefficient * previous["value"]
    count = query_characteristic_count(host, _ResolutionQuantitySource(controller=controller, ref=stack_ref), spec.quantity)
    if type(count) is not int or count < 0:
        raise PublicQueryAmountError("Public quantity is unavailable or malformed")
    if cache is not None:
        cache[spec.binding_id] = {"controller":controller,"quantity":spec.quantity.to_dict(),"value":count}
    return spec.coefficient * count


def resolve_cast_x_amount(value: Mapping[str, Any], item: Any) -> int:
    """Consume, never choose or price, the cost owner's persisted stack X."""
    spec = CastXAmountSpec.from_dict(value)
    amount = getattr(item, "x_value", None)
    if amount is None:
        amount = 0
    if type(amount) is not int or amount < 0:
        raise PublicQueryAmountError("Announced cast X is malformed")
    return spec.coefficient * amount


__all__ = ["PublicQueryAmountHost", "resolve_public_query_amount", "resolve_cast_x_amount"]
