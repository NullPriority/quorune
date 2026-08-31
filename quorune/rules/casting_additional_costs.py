from __future__ import annotations

"""Typed nonmana casting-cost descriptors and candidate queries."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from ..additional_cost_vocabulary import (
    DISCARD_ONE_COST,
    EXILE_ONE_FROM_BATTLEFIELD_COST,
    EXILE_ONE_FROM_GRAVEYARD_COST,
    FIXED_ZONE_CHANGE_COST_CONTRACTS,
    FIXED_ZONE_CHANGE_COST_OPERATIONS,
    RETURN_ONE_TO_OWNER_HAND_COST,
    SACRIFICE_COST_KIND,
    SACRIFICE_ONE_COST,
    ZONE_CHANGE_COST_KIND,
)
from ..object_predicate import ObjectQuerySpec
from ..object_query import object_query_result, query_objects


class AdditionalCostError(ValueError):
    """A casting additional-cost descriptor or selection is malformed."""


class AdditionalCostQueryHost(Protocol):
    state: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


_FIXED_COUNTER_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "counter",
        "amount",
        "choice_field",
        "predicate",
    }
)
_FIXED_SACRIFICE_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "count",
        "choice_field",
        "predicate",
    }
)
_FIXED_ZONE_CHANGE_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "operation",
        "count",
        "choice_field",
        "predicate",
    }
)
_PERMANENT_CARD_TYPES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "land",
        "planeswalker",
    }
)
_CARD_TYPES = _PERMANENT_CARD_TYPES | frozenset(
    {"instant", "kindred", "sorcery"}
)


def _unbound_creature_you_control_query() -> ObjectQuerySpec:
    return ObjectQuerySpec(
        zones=("battlefield",),
        controller="$actor",
        types_all=("creature",),
        known_to_actor=True,
    )


def _unbound_permanent_you_control_query(
    types_any: tuple[str, ...] = (),
) -> ObjectQuerySpec:
    return ObjectQuerySpec(
        zones=("battlefield",),
        controller="$actor",
        types_any=types_any,
        known_to_actor=True,
    )


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementAdditionalCost:
    """Place fixed counters on one chosen controlled creature as a cost."""

    counter_name: str
    amount: int
    choice_field: str
    predicate: ObjectQuerySpec
    schema_version: int = 1
    kind: str = "counter_placement"

    def __post_init__(self) -> None:
        if type(self.counter_name) is not str:
            raise AdditionalCostError(
                "Counter additional costs require a counter name"
            )
        normalized = " ".join(self.counter_name.casefold().split())
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AdditionalCostError(
                "Counter additional-cost schema version is unsupported"
            )
        if self.kind != "counter_placement":
            raise AdditionalCostError(
                "Counter additional-cost kind is unsupported"
            )
        if not normalized:
            raise AdditionalCostError(
                "Counter additional costs require a counter name"
            )
        if type(self.amount) is not int or self.amount <= 0:
            raise AdditionalCostError(
                "Counter additional-cost amount must be a positive integer"
            )
        if type(self.choice_field) is not str or (
            not self.choice_field
            or self.choice_field != self.choice_field.strip()
        ):
            raise AdditionalCostError(
                "Counter additional costs require a canonical choice field"
            )
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise AdditionalCostError(
                "Counter additional costs require a typed object predicate"
            )
        if self.predicate != _unbound_creature_you_control_query():
            raise AdditionalCostError(
                "Counter additional-cost predicate is outside the closed family"
            )
        object.__setattr__(self, "counter_name", normalized)

    @classmethod
    def from_descriptor(
        cls, value: Mapping[str, Any]
    ) -> "FixedCounterPlacementAdditionalCost":
        if not isinstance(value, Mapping) or set(value) != _FIXED_COUNTER_FIELDS:
            raise AdditionalCostError(
                "Counter additional-cost descriptor fields are closed"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(value["predicate"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AdditionalCostError(
                "Counter additional-cost predicate is malformed"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            kind=value["kind"],
            counter_name=value["counter"],
            amount=value["amount"],
            choice_field=value["choice_field"],
            predicate=predicate,
        )

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "counter": self.counter_name,
            "amount": self.amount,
            "choice_field": self.choice_field,
            "predicate": self.predicate.to_dict(),
        }

    def bound_predicate(self, actor: str) -> ObjectQuerySpec:
        if type(actor) is not str or not actor:
            raise AdditionalCostError(
                "Counter additional-cost actor must be nonempty"
            )
        return replace(self.predicate, controller=actor)


@dataclass(frozen=True, slots=True)
class FixedSacrificeAdditionalCost:
    """Sacrifice exactly one controlled permanent matching a closed query."""

    choice_field: str
    predicate: ObjectQuerySpec
    schema_version: int = 1
    kind: str = SACRIFICE_COST_KIND
    count: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AdditionalCostError(
                "Sacrifice additional-cost schema version is unsupported"
            )
        if self.kind != SACRIFICE_COST_KIND:
            raise AdditionalCostError(
                "Sacrifice additional-cost kind is unsupported"
            )
        if type(self.count) is not int or self.count != 1:
            raise AdditionalCostError(
                "Fixed sacrifice additional costs require exactly one object"
            )
        if self.choice_field != "sacrifice_cards":
            raise AdditionalCostError(
                "Sacrifice additional costs require the canonical choice field"
            )
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise AdditionalCostError(
                "Sacrifice additional costs require a typed object predicate"
            )
        expected = _unbound_permanent_you_control_query(
            self.predicate.types_any
        )
        if self.predicate != expected:
            raise AdditionalCostError(
                "Sacrifice additional-cost predicate is outside the closed family"
            )
        if not set(self.predicate.types_any).issubset(_PERMANENT_CARD_TYPES):
            raise AdditionalCostError(
                "Sacrifice additional-cost types must be permanent card types"
            )
        if len(self.predicate.types_any) > 2:
            raise AdditionalCostError(
                "Sacrifice additional costs support at most two permanent types"
            )

    @classmethod
    def from_descriptor(
        cls, value: Mapping[str, Any]
    ) -> "FixedSacrificeAdditionalCost":
        if not isinstance(value, Mapping) or set(value) != _FIXED_SACRIFICE_FIELDS:
            raise AdditionalCostError(
                "Sacrifice additional-cost descriptor fields are closed"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(value["predicate"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AdditionalCostError(
                "Sacrifice additional-cost predicate is malformed"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            kind=value["kind"],
            count=value["count"],
            choice_field=value["choice_field"],
            predicate=predicate,
        )

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "count": self.count,
            "choice_field": self.choice_field,
            "predicate": self.predicate.to_dict(),
        }

    def bound_predicate(self, actor: str) -> ObjectQuerySpec:
        if type(actor) is not str or not actor:
            raise AdditionalCostError(
                "Sacrifice additional-cost actor must be nonempty"
            )
        return replace(self.predicate, controller=actor)


@dataclass(frozen=True, slots=True)
class FixedZoneChangeAdditionalCost:
    """Move exactly one selected object between operation-owned zones."""

    operation: str
    choice_field: str
    predicate: ObjectQuerySpec
    schema_version: int = 1
    kind: str = ZONE_CHANGE_COST_KIND
    count: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AdditionalCostError(
                "Zone-change additional-cost schema version is unsupported"
            )
        if self.kind != ZONE_CHANGE_COST_KIND:
            raise AdditionalCostError(
                "Zone-change additional-cost kind is unsupported"
            )
        if self.operation not in FIXED_ZONE_CHANGE_COST_OPERATIONS:
            raise AdditionalCostError(
                "Zone-change additional-cost operation is unsupported"
            )
        if type(self.count) is not int or self.count != 1:
            raise AdditionalCostError(
                "Fixed zone-change additional costs require one object"
            )
        origin, _, expected_field = FIXED_ZONE_CHANGE_COST_CONTRACTS[
            self.operation
        ]
        if self.choice_field != expected_field:
            raise AdditionalCostError(
                "Zone-change additional cost has a noncanonical choice field"
            )
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise AdditionalCostError(
                "Zone-change additional costs require a typed object predicate"
            )
        expected_owner = "$actor" if origin != "battlefield" else None
        expected_controller = "$actor" if origin == "battlefield" else None
        if (
            self.predicate.zones != (origin,)
            or self.predicate.owner != expected_owner
            or self.predicate.controller != expected_controller
            or self.predicate.known_to_actor is not True
            or self.predicate.include_phased_out
            or self.predicate.keywords_all
            or self.predicate.excluded_controllers
            or self.predicate.excluded_subtypes
            or self.predicate.colorless is not None
            or self.predicate.minimum_color_count is not None
            or self.predicate.state_predicate is not None
            or (
                self.predicate.token is not None
                and origin != "battlefield"
            )
            or self.predicate.tapped is not None
            or self.predicate.exclude_ref is not None
        ):
            raise AdditionalCostError(
                "Zone-change additional-cost predicate is outside the closed family"
            )
        represented_types = (
            set(self.predicate.types_all)
            | set(self.predicate.types_any)
            | set(self.predicate.excluded_types)
        )
        allowed_types = (
            _PERMANENT_CARD_TYPES if origin == "battlefield" else _CARD_TYPES
        )
        if not represented_types.issubset(allowed_types):
            raise AdditionalCostError(
                "Zone-change additional-cost card types are unsupported"
            )
        if self.predicate.types_all and self.predicate.types_any:
            raise AdditionalCostError(
                "Zone-change costs cannot combine type conjunction and union"
            )
        if self.predicate.subtypes_all and self.predicate.subtypes_any:
            raise AdditionalCostError(
                "Zone-change costs cannot combine subtype conjunction and union"
            )
        if self.predicate.colors_all and self.predicate.colors_any:
            raise AdditionalCostError(
                "Zone-change costs cannot combine color conjunction and union"
            )
        if not set(self.predicate.supertypes_all).issubset(
            {"basic", "legendary", "snow"}
        ):
            raise AdditionalCostError(
                "Zone-change additional-cost supertypes are unsupported"
            )
        if not set(
            self.predicate.colors_all + self.predicate.colors_any
        ).issubset({"W", "U", "B", "R", "G", "C"}):
            raise AdditionalCostError(
                "Zone-change additional-cost colors are unsupported"
            )

    @classmethod
    def from_descriptor(
        cls, value: Mapping[str, Any]
    ) -> "FixedZoneChangeAdditionalCost":
        if not isinstance(value, Mapping) or set(value) != _FIXED_ZONE_CHANGE_FIELDS:
            raise AdditionalCostError(
                "Zone-change additional-cost descriptor fields are closed"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(value["predicate"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AdditionalCostError(
                "Zone-change additional-cost predicate is malformed"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            kind=value["kind"],
            operation=value["operation"],
            count=value["count"],
            choice_field=value["choice_field"],
            predicate=predicate,
        )

    @classmethod
    def from_legacy_sacrifice(
        cls, cost: FixedSacrificeAdditionalCost
    ) -> "FixedZoneChangeAdditionalCost":
        return cls(
            operation=SACRIFICE_ONE_COST,
            choice_field=cost.choice_field,
            predicate=cost.predicate,
        )

    @property
    def origin_zone(self) -> str:
        return FIXED_ZONE_CHANGE_COST_CONTRACTS[self.operation][0]

    @property
    def destination_zone(self) -> str:
        return FIXED_ZONE_CHANGE_COST_CONTRACTS[self.operation][1]

    @property
    def log_kind(self) -> str:
        return {
            DISCARD_ONE_COST: "discard",
            SACRIFICE_ONE_COST: "sacrifice",
            EXILE_ONE_FROM_GRAVEYARD_COST: "exile",
            EXILE_ONE_FROM_BATTLEFIELD_COST: "exile",
            RETURN_ONE_TO_OWNER_HAND_COST: "return",
        }[self.operation]

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "operation": self.operation,
            "count": self.count,
            "choice_field": self.choice_field,
            "predicate": self.predicate.to_dict(),
        }

    def bound_predicate(self, actor: str) -> ObjectQuerySpec:
        if type(actor) is not str or not actor:
            raise AdditionalCostError(
                "Zone-change additional-cost actor must be nonempty"
            )
        if self.origin_zone == "battlefield":
            return replace(self.predicate, controller=actor)
        return replace(self.predicate, owner=actor)


def fixed_counter_additional_cost(
    value: Mapping[str, Any],
) -> FixedCounterPlacementAdditionalCost | None:
    if not isinstance(value, Mapping):
        raise AdditionalCostError("Additional costs must be objects")
    if value.get("kind") != "counter_placement":
        return None
    return FixedCounterPlacementAdditionalCost.from_descriptor(value)


def fixed_sacrifice_additional_cost(
    value: Mapping[str, Any],
) -> FixedSacrificeAdditionalCost | None:
    if not isinstance(value, Mapping):
        raise AdditionalCostError("Additional costs must be objects")
    if (
        value.get("kind") != SACRIFICE_COST_KIND
        or "schema_version" not in value
    ):
        return None
    return FixedSacrificeAdditionalCost.from_descriptor(value)


def fixed_zone_change_additional_cost(
    value: Mapping[str, Any],
) -> FixedZoneChangeAdditionalCost | None:
    if not isinstance(value, Mapping):
        raise AdditionalCostError("Additional costs must be objects")
    if value.get("kind") == ZONE_CHANGE_COST_KIND:
        return FixedZoneChangeAdditionalCost.from_descriptor(value)
    legacy = fixed_sacrifice_additional_cost(value)
    if legacy is None:
        return None
    return FixedZoneChangeAdditionalCost.from_legacy_sacrifice(legacy)


def fixed_counter_cost_candidates(
    host: AdditionalCostQueryHost,
    *,
    actor: str,
    cost: FixedCounterPlacementAdditionalCost,
) -> tuple[str, ...]:
    """Return public candidate refs using effective characteristics."""

    rows = []
    for object_id in host.state.players[actor].zones["battlefield"]:
        card = host.state.cards[object_id]
        effective = host._effective_card_data(card)
        rows.append(
            object_query_result(
                card,
                effective,
                type_parts=host._type_parts(
                    str(effective.get("type_line") or "")
                ),
                known_to_actor=True,
                attached_to_ref=None,
            )
        )
    return tuple(
        row.ref
        for row in query_objects(rows, cost.bound_predicate(actor))
    )


def fixed_sacrifice_cost_candidates(
    host: AdditionalCostQueryHost,
    *,
    actor: str,
    cost: FixedSacrificeAdditionalCost,
    exclude_object_id: str | None = None,
) -> tuple[str, ...]:
    """Return controlled sacrifice candidates using effective characteristics."""

    return fixed_zone_change_cost_candidates(
        host,
        actor=actor,
        cost=FixedZoneChangeAdditionalCost.from_legacy_sacrifice(cost),
        exclude_object_id=exclude_object_id,
    )


def fixed_zone_change_cost_candidates(
    host: AdditionalCostQueryHost,
    *,
    actor: str,
    cost: FixedZoneChangeAdditionalCost,
    exclude_object_id: str | None = None,
) -> tuple[str, ...]:
    """Return operation-owned candidates through one immutable query path."""

    rows = []
    for object_id in host.state.players[actor].zones[cost.origin_zone]:
        if object_id == exclude_object_id:
            continue
        card = host.state.cards[object_id]
        effective = host._effective_card_data(card)
        rows.append(
            object_query_result(
                card,
                effective,
                type_parts=host._type_parts(
                    str(effective.get("type_line") or "")
                ),
                known_to_actor=True,
                attached_to_ref=None,
            )
        )
    return tuple(
        row.ref
        for row in query_objects(rows, cost.bound_predicate(actor))
    )


def legacy_additional_cost_candidates(
    host: AdditionalCostQueryHost,
    *,
    actor: str,
    source: Any,
    specification: Mapping[str, Any],
) -> tuple[str, ...]:
    """Isolate the remaining pre-typed discard/sacrifice compatibility path."""

    kind = str(specification.get("kind") or "")
    zone = str(
        specification.get("zone")
        or ("hand" if kind == "discard" else "battlefield")
    )
    types = {
        str(value).casefold()
        for value in (
            specification.get("types_any")
            or (
                [specification["card_type"]]
                if specification.get("card_type")
                else []
            )
        )
    }
    candidates: list[str] = []
    for object_id in host.state.players[actor].zones.get(zone, []):
        card = host.state.cards[object_id]
        if zone == "battlefield":
            if card.controller != actor or card.phased_out:
                continue
        elif card.owner != actor:
            continue
        if (
            specification.get("exclude_source")
            or specification.get("another")
        ) and card.object_id == source.object_id:
            continue
        if types:
            effective = host._effective_card_data(card)
            card_types, _, _ = host._type_parts(
                str(effective.get("type_line") or "")
            )
            if types.isdisjoint(card_types):
                continue
        candidates.append(card.ref)
    return tuple(candidates)


__all__ = [
    "AdditionalCostError",
    "AdditionalCostQueryHost",
    "FixedCounterPlacementAdditionalCost",
    "FixedSacrificeAdditionalCost",
    "FixedZoneChangeAdditionalCost",
    "fixed_counter_additional_cost",
    "fixed_counter_cost_candidates",
    "fixed_sacrifice_additional_cost",
    "fixed_sacrifice_cost_candidates",
    "fixed_zone_change_additional_cost",
    "fixed_zone_change_cost_candidates",
    "legacy_additional_cost_candidates",
]
