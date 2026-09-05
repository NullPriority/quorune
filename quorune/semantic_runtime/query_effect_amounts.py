from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..dynamic_characteristics import query_characteristic_count
from ..query_effect_amount_model import (
    PublicQueryAmountError,
    PublicQueryAmountSpec,
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
    count = query_characteristic_count(
        host,
        _ResolutionQuantitySource(controller=controller, ref=stack_ref),
        spec.quantity,
    )
    return spec.coefficient * count


__all__ = ["PublicQueryAmountHost", "resolve_public_query_amount"]
