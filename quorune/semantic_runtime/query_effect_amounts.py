from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

from ..dynamic_characteristics import query_characteristic_count
from ..live_battlefield_quantities import current_live_battlefield_count, current_live_attacking_shared_type_count
from ..live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
from ..query_effect_amount_model import (
    PublicQueryAmountError,
    PublicQueryAmountSpec,
    CastXAmountSpec,
    ATTACK_SHARED_TYPE_AMOUNT_VERSION,
    SOURCE_SHARED_TYPE_AMOUNT_VERSION,
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


def _live_quantity_source(host, item, quantity):
    """Exclude the original source object, never its later incarnation."""
    source_id = getattr(item, 'source_object_id', None) or getattr(item, 'card_object_id', None)
    source = host.state.cards.get(source_id)
    context = getattr(item, 'context', None)
    identity = context.get('source_logical_object_id') if isinstance(context, Mapping) else None
    if type(identity) is not str or not identity:
        raise PublicQueryAmountError('Source-excluding quantity requires its captured logical identity')
    if source is None or source.logical_object_id != identity:
        quantity = replace(quantity, exclude_source=False)
    return quantity, source.ref if source is not None else None


def _relative_quantity(host, item, spec: PublicQueryAmountSpec):
    quantity = spec.quantity
    if spec.schema_version == 3:
        from ..event_counter_reference import resolve_event_counter_recipient

        if item.context.get("event") != "creature.attacks":
            raise PublicQueryAmountError("Attack-relative counts require a sealed attack occurrence")
        reference = resolve_event_counter_recipient(host, item)
        return (replace(quantity, exclude_source=False), None) if reference is None else (quantity, reference)
    if quantity.exclude_source:
        return _live_quantity_source(host, item, quantity)
    return quantity, None


def _current_shared_source_type_count(host, item, spec, controller):
    from ..continuous_effect_model import ContinuousObjectIdentity
    from ..object_query import query_objects
    from ..rules.shared_type_library import materialize_shared_type_source
    from ..shared_creature_type_model import (
        SharedCreatureTypeObject, canonical_creature_types, shared_creature_type_count,
    )

    if controller != item.controller:
        raise PublicQueryAmountError("A shared source count requires the ability controller")
    source = materialize_shared_type_source(host, item)
    query = replace(spec.quantity.query, controller=controller)
    peers = tuple(
        SharedCreatureTypeObject(
            ContinuousObjectIdentity(row.object_id, row.logical_object_id),
            canonical_creature_types(row.subtypes),
        )
        for row in query_objects(host.affected_permanent_object_rows(controller), query)
    )
    return shared_creature_type_count(peers, recipient=source.identity, creature_types=source.creature_types)


def _current_public_amount_count(host, item, spec, controller, stack_ref):
    if spec.schema_version == SOURCE_SHARED_TYPE_AMOUNT_VERSION:
        return _current_shared_source_type_count(host, item, spec, controller)
    if spec.schema_version == ATTACK_SHARED_TYPE_AMOUNT_VERSION:
        from ..event_counter_reference import resolve_event_counter_recipient

        if item.context.get("event") != "creature.attacks":
            raise PublicQueryAmountError("Shared-type attack counts require a sealed attack occurrence")
        source_ref = resolve_event_counter_recipient(host, item)
        if source_ref is None:
            return 0
        return current_live_attacking_shared_type_count(host, controller, spec.quantity, source_ref=source_ref)
    quantity, source_ref = _relative_quantity(host, item, spec)
    if isinstance(quantity, LiveBattlefieldQuantitySpec):
        return current_live_battlefield_count(host, controller, quantity, source_ref=source_ref)
    return query_characteristic_count(
        host, _ResolutionQuantitySource(controller=controller, ref=source_ref or stack_ref), quantity,
    )


def resolve_public_query_amount(
    host: PublicQueryAmountHost,
    value: Mapping[str, Any],
    item: Any,
    *,
    query_player: str | None = None,
) -> int:
    """Resolve for an explicit query player or the stack's locked controller."""

    spec = PublicQueryAmountSpec.from_dict(value)
    controller = getattr(item, "controller", None) if query_player is None else query_player
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
    try:
        count = _current_public_amount_count(host, item, spec, controller, stack_ref)
    except ValueError as exc:
        raise PublicQueryAmountError(str(exc)) from exc
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
        if spec.schema_version == 3:
            raise PublicQueryAmountError("Cast-X bonus requires an actual announced X")
        amount = 0
    if type(amount) is not int or amount < 0:
        raise PublicQueryAmountError("Announced cast X is malformed")
    return spec.coefficient * amount + spec.bonus


__all__ = ["PublicQueryAmountHost", "resolve_public_query_amount", "resolve_cast_x_amount"]
