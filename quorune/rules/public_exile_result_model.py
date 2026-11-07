"""Closed counts of cards exiled by one public graveyard move."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import (
    CommittedQuantityFollowup,
    fixed_followup_capabilities,
    fixed_quantity_effect,
    quantity_followup_from_count,
)

PUBLIC_EXILE_RESULT_CAPABILITY = "card.exile.public_graveyard_result_quantity"
PUBLIC_EXILE_RESULT_MECHANIC = "committed-graveyard-exile-quantity"

_SCOPES = (
    ObjectQuerySpec(zones=("graveyard",), token=False),
    ObjectQuerySpec(zones=("graveyard",), types_all=("creature",), token=False),
    ObjectQuerySpec(zones=("graveyard",), types_any=("artifact", "land"), token=False),
)


@dataclass(frozen=True, slots=True)
class PublicExileFollowup:
    query: ObjectQuerySpec
    effect: FrozenMap

    def __post_init__(self) -> None:
        if self.query not in _SCOPES:
            raise ValueError("Graveyard exile result needs a closed public card scope")
        effect = fixed_quantity_effect(self.effect)
        if effect["op"] not in ("life", "create_token", "place_counters"):
            raise ValueError("Graveyard exile result instruction is unsupported")
        object.__setattr__(self, "effect", effect)

    def validate_move(self, spec) -> None:
        from ..public_zone_moves import (
            PublicZoneDestination, PublicZoneMoveSetSpec, PublicZoneOrigin,
            PublicZoneRelationAxis, PublicZoneSeatRelation,
        )

        if (
            not isinstance(spec, PublicZoneMoveSetSpec)
            or spec.origin is not PublicZoneOrigin.GRAVEYARD
            or spec.destination is not PublicZoneDestination.EXILE
            or spec.relation_axis is not PublicZoneRelationAxis.OWNER
            or spec.seat_relation is not PublicZoneSeatRelation.TARGET_PLAYER
            or spec.exclude_source
            or spec.query not in (ObjectQuerySpec(zones=("graveyard",)), _SCOPES[1])
        ):
            raise ValueError("Graveyard exile result needs one fixed targeted graveyard move")

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        return (PUBLIC_EXILE_RESULT_CAPABILITY, *fixed_followup_capabilities(self.effect))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "query": self.query.canonical_dict(), "effect": thaw_value(self.effect)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicExileFollowup:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "query", "effect"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
        ):
            raise ValueError("Graveyard exile follow-up descriptor is malformed")
        return cls(ObjectQuerySpec.from_dict(value["query"]), value["effect"])

    def from_committed_count(self, count: int) -> CommittedQuantityFollowup:
        return quantity_followup_from_count(self.effect, count)
