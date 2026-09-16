from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Any, Mapping

from .affected_permanents import PermanentControllerRelation
from .object_predicate import ObjectQueryError, ObjectQuerySpec
from .util import stable_json


class FixedDamageSetError(ValueError):
    """A fixed simultaneous damage-set descriptor or snapshot is invalid."""


class PlayerDamageRelation(str, Enum):
    ALL = "all"
    OPPONENTS = "opponents"


_DAMAGEABLE_TYPES = frozenset({"battle", "creature", "planeswalker"})
_PLAYER_GROUP_FIELDS = frozenset({"kind", "relation"})
_LEGACY_PERMANENT_GROUP_FIELDS = frozenset(
    {"kind", "controller_relation", "target_controller", "query"}
)
_PERMANENT_GROUP_FIELDS = _LEGACY_PERMANENT_GROUP_FIELDS | {"exclude_source"}


def require_nonempty_string(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise FixedDamageSetError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class PlayerDamageGroup:
    relation: PlayerDamageRelation

    def __post_init__(self) -> None:
        if not isinstance(self.relation, PlayerDamageRelation):
            raise FixedDamageSetError("Player damage relation is unsupported")

    def to_dict(self) -> dict[str, str]:
        return {"kind": "players", "relation": self.relation.value}


@dataclass(frozen=True, slots=True)
class PermanentDamageGroup:
    query: ObjectQuerySpec
    controller_relation: PermanentControllerRelation = (
        PermanentControllerRelation.ANY
    )
    target_controller: str | None = None
    exclude_source: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.query, ObjectQuerySpec):
            raise FixedDamageSetError(
                "Permanent damage groups require a typed object query"
            )
        if not isinstance(
            self.controller_relation, PermanentControllerRelation
        ):
            raise FixedDamageSetError(
                "Permanent controller relation is unsupported"
            )
        if self.query.zones != ("battlefield",):
            raise FixedDamageSetError(
                "Fixed permanent damage queries are battlefield-only"
            )
        if self.query.owner is not None or self.query.controller is not None:
            raise FixedDamageSetError(
                "Fixed permanent damage uses a typed controller relation"
            )
        if self.query.include_phased_out:
            raise FixedDamageSetError(
                "Fixed permanent damage excludes phased-out objects"
            )
        if self.query.known_to_actor is not None:
            raise FixedDamageSetError(
                "Public battlefield damage queries do not use knowledge predicates"
            )
        if type(self.exclude_source) is not bool:
            raise FixedDamageSetError(
                "Fixed permanent damage source exclusion must be boolean"
            )
        if self.query.exclude_ref is not None:
            raise FixedDamageSetError(
                "Fixed permanent damage uses its typed source-exclusion flag"
            )
        if self.controller_relation is PermanentControllerRelation.ACTOR:
            raise FixedDamageSetError(
                "Fixed permanent damage does not support actor-only groups"
            )
        represented_by_all = bool(
            set(self.query.types_all).intersection(_DAMAGEABLE_TYPES)
        )
        represented_by_any = bool(self.query.types_any) and set(
            self.query.types_any
        ).issubset(_DAMAGEABLE_TYPES)
        if not represented_by_all and not represented_by_any:
            raise FixedDamageSetError(
                "Fixed permanent damage queries must select only damageable types"
            )
        if self.controller_relation is PermanentControllerRelation.TARGET_PLAYER:
            require_nonempty_string(
                self.target_controller,
                field="Permanent target controller",
            )
        elif self.target_controller is not None:
            raise FixedDamageSetError(
                "Only target-player damage groups accept a target controller"
            )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "kind": "permanents",
            "controller_relation": self.controller_relation.value,
            "target_controller": self.target_controller,
            "query": self.query.canonical_dict(),
        }
        if self.exclude_source:
            payload["exclude_source"] = True
        return payload


FixedDamageGroup = PlayerDamageGroup | PermanentDamageGroup


def _group_from_dict(value: Mapping[str, Any]) -> FixedDamageGroup:
    if not isinstance(value, Mapping):
        raise FixedDamageSetError("Fixed damage groups must be objects")
    kind = value.get("kind")
    if kind == "players":
        if frozenset(value) != _PLAYER_GROUP_FIELDS:
            raise FixedDamageSetError(
                "Player damage group fields must be exactly kind and relation"
            )
        try:
            return PlayerDamageGroup(PlayerDamageRelation(value["relation"]))
        except (TypeError, ValueError) as exc:
            raise FixedDamageSetError(
                "Player damage relation is unsupported"
            ) from exc
    if kind != "permanents":
        raise FixedDamageSetError("Fixed damage group kind is unsupported")
    fields = frozenset(value)
    if fields not in {
        _LEGACY_PERMANENT_GROUP_FIELDS,
        _PERMANENT_GROUP_FIELDS,
    }:
        raise FixedDamageSetError(
            "Permanent damage group fields are incomplete or unknown"
        )
    try:
        relation = PermanentControllerRelation(value["controller_relation"])
        query = ObjectQuerySpec.from_dict(value["query"])
    except (KeyError, TypeError, ValueError, ObjectQueryError) as exc:
        raise FixedDamageSetError(
            "Permanent damage group descriptor is malformed"
        ) from exc
    return PermanentDamageGroup(
        query=query,
        controller_relation=relation,
        target_controller=value["target_controller"],
        exclude_source=value.get("exclude_source", False),
    )


@dataclass(frozen=True, slots=True)
class FixedDamageSetSpec:
    groups: tuple[FixedDamageGroup, ...]

    def __post_init__(self) -> None:
        groups = tuple(self.groups)
        if not groups:
            raise FixedDamageSetError(
                "Fixed damage sets require at least one recipient group"
            )
        if any(
            not isinstance(group, (PlayerDamageGroup, PermanentDamageGroup))
            for group in groups
        ):
            raise FixedDamageSetError(
                "Fixed damage sets require typed recipient groups"
            )
        serialized = [stable_json(group.to_dict()) for group in groups]
        if len(serialized) != len(set(serialized)):
            raise FixedDamageSetError(
                "Fixed damage sets cannot repeat an identical recipient group"
            )
        object.__setattr__(self, "groups", groups)

    def to_dict(self) -> dict[str, Any]:
        return {"groups": [group.to_dict() for group in self.groups]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedDamageSetSpec":
        if not isinstance(value, Mapping) or frozenset(value) != {"groups"}:
            raise FixedDamageSetError(
                "Fixed damage set fields must be exactly groups"
            )
        raw_groups = value["groups"]
        if not isinstance(raw_groups, (list, tuple)):
            raise FixedDamageSetError("Fixed damage groups must be an array")
        return cls(tuple(_group_from_dict(group) for group in raw_groups))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


__all__ = [
    "FixedDamageGroup",
    "FixedDamageSetError",
    "FixedDamageSetSpec",
    "PermanentControllerRelation",
    "PermanentDamageGroup",
    "PlayerDamageGroup",
    "PlayerDamageRelation",
    "require_nonempty_string",
]
