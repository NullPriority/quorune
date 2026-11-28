"""Closed follow-ups to committed public exile results."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import (
    CommittedQuantityFollowup,
    fixed_followup_capabilities,
    fixed_quantity_effect,
    quantity_followup_from_count,
)

PUBLIC_EXILE_RESULT_CAPABILITY = "card.exile.public_graveyard_result_quantity"
PUBLIC_EXILE_RESULT_MECHANIC = "committed-graveyard-exile-quantity"
ATTACKING_EXILE_SEARCH_CAPABILITY = "zone.exile.attacking_creatures_optional_basic_search"
ATTACKING_EXILE_SEARCH_MECHANIC = "committed-attacking-creature-exile-search"
ATTACKING_CREATURE_EXILE_QUERY = ObjectQuerySpec(
    zones=("battlefield",), types_all=("creature",), state_predicate=PermanentStatePredicateSpec(attacking=True),
)
_ATTACKING_SEARCH = FrozenMap({"kind": "affected_player_basic_land_search"})
_CONTROLLER_LAND_SEARCH = FrozenMap({"kind": "former_controller_basic_land_search"})

_SCOPES = (
    ObjectQuerySpec(zones=("graveyard",), token=False),
    ObjectQuerySpec(zones=("graveyard",), types_all=("creature",), token=False),
    ObjectQuerySpec(zones=("graveyard",), types_any=("artifact", "land"), token=False),
)


@dataclass(frozen=True, slots=True)
class PublicExileFollowup:
    query: ObjectQuerySpec
    effect: FrozenMap
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3}:
            raise ValueError("Unknown public exile follow-up version")
        if self.schema_version == 3:
            from .affected_library_search_batch import EXILE_SEARCH_QUERY

            if (self.query != EXILE_SEARCH_QUERY or not isinstance(self.effect, Mapping)
                    or FrozenMap(self.effect) != _CONTROLLER_LAND_SEARCH):
                raise ValueError("Controller land search requires its exact creature-exile follow-up")
            object.__setattr__(self, "effect", _CONTROLLER_LAND_SEARCH)
            return
        if self.schema_version == 2:
            if self.query != ATTACKING_CREATURE_EXILE_QUERY or not isinstance(self.effect, Mapping) or FrozenMap(self.effect) != _ATTACKING_SEARCH:
                raise ValueError("Attacking creature exile requires its exact optional affected-player search")
            object.__setattr__(self, "effect", _ATTACKING_SEARCH)
            return
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

        if self.schema_version == 3:
            if spec != PublicZoneMoveSetSpec(
                query=self.query, origin=PublicZoneOrigin.BATTLEFIELD, destination=PublicZoneDestination.EXILE,
                seat_relation=PublicZoneSeatRelation.OPPONENTS,
            ):
                raise ValueError("Controller compensation requires the original opposing creature set")
            return
        if self.schema_version == 2:
            if not isinstance(spec, PublicZoneMoveSetSpec) or spec != PublicZoneMoveSetSpec(
                query=ATTACKING_CREATURE_EXILE_QUERY, origin=PublicZoneOrigin.BATTLEFIELD,
                destination=PublicZoneDestination.EXILE, seat_relation=PublicZoneSeatRelation.TARGET_PLAYER,
                target_seat=spec.target_seat,
            ):
                raise ValueError("The optional search requires the original public attacking creature exile")
            return
        if (
            not isinstance(spec, PublicZoneMoveSetSpec)
            or spec.origin is not PublicZoneOrigin.GRAVEYARD
            or spec.destination is not PublicZoneDestination.EXILE
            or spec.relation_axis is not PublicZoneRelationAxis.OWNER
            or spec.exclude_source
        ):
            raise ValueError("Graveyard exile result needs a fixed public graveyard exile")
        targeted = (
            spec.seat_relation is PublicZoneSeatRelation.TARGET_PLAYER
            and spec.query in (ObjectQuerySpec(zones=("graveyard",)), _SCOPES[1])
        )
        all_graveyards = (
            spec.seat_relation is PublicZoneSeatRelation.ANY
            and spec.query == _SCOPES[1]
            and self.query == _SCOPES[0]
            and dict(self.effect) == {"op": "life", "player": "$controller", "delta": 1}
        )
        if not (targeted or all_graveyards):
            raise ValueError("Graveyard exile result has an unsupported move and count scope")

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        if self.schema_version == 3:
            from .affected_library_search_batch import exile_land_search_dependencies

            return exile_land_search_dependencies()
        if self.schema_version == 2:
            return (ATTACKING_EXILE_SEARCH_CAPABILITY, "effect.choice.optional_fixed", "library.search.fixed_counted",
                    "zone.entry.tapped_state", "target.revalidate_resolution")
        return (PUBLIC_EXILE_RESULT_CAPABILITY, *fixed_followup_capabilities(self.effect))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "query": self.query.canonical_dict(), "effect": thaw_value(self.effect)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicExileFollowup:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "query", "effect"}
            or type(value["schema_version"]) is not int or value["schema_version"] not in {1, 2, 3}
        ):
            raise ValueError("Public exile follow-up descriptor is malformed")
        return cls(ObjectQuerySpec.from_dict(value["query"]), value["effect"], value["schema_version"])

    @classmethod
    def attacking_creature_search(cls):
        return cls(ATTACKING_CREATURE_EXILE_QUERY, _ATTACKING_SEARCH, schema_version=2)

    @classmethod
    def controller_land_search(cls):
        from .affected_library_search_batch import EXILE_SEARCH_QUERY

        return cls(EXILE_SEARCH_QUERY, _CONTROLLER_LAND_SEARCH, schema_version=3)

    def from_committed_count(self, count: int, *, target_player: str | None = None) -> CommittedQuantityFollowup:
        if self.schema_version == 2:
            from ..library_search_model import committed_attacking_exile_search_effect

            search = committed_attacking_exile_search_effect(target_player, count)
            return CommittedQuantityFollowup((FrozenMap({
                "op": "offer_optional_effect", "player": target_player, "effects": [search],
            }),))
        return quantity_followup_from_count(self.effect, count)
