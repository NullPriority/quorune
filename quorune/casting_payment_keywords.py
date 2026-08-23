from __future__ import annotations

"""Closed typed specifications for printed casting-payment keywords."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .convoke import canonical_mana_requirements
from .object_predicate import ObjectQueryError, ObjectQuerySpec


AFFINITY_MECHANIC_ID = "affinity"
DELVE_MECHANIC_ID = "delve"
IMPROVISE_MECHANIC_ID = "improvise"


class CastingPaymentKeywordError(ValueError):
    """A printed casting-payment keyword descriptor is malformed."""


def _query(**values: Any) -> ObjectQuerySpec:
    return ObjectQuerySpec(**values)


_SUBTYPE_QUALITIES = {
    "allies": "ally",
    "birds": "bird",
    "cats": "cat",
    "citizens": "citizen",
    "daleks": "dalek",
    "equipment": "equipment",
    "foods": "food",
    "forests": "forest",
    "frogs": "frog",
    "gates": "gate",
    "humans": "human",
    "islands": "island",
    "knights": "knight",
    "lizards": "lizard",
    "mountains": "mountain",
    "plains": "plains",
    "slivers": "sliver",
    "spirits": "spirit",
    "swamps": "swamp",
    "towns": "town",
}
_AFFINITY_QUERIES: dict[str, tuple[ObjectQuerySpec, ...]] = {
    "artifact creatures": (_query(types_all=("artifact", "creature")),),
    "artifacts": (_query(types_all=("artifact",)),),
    "creatures": (_query(types_all=("creature",)),),
    "enchantments": (_query(types_all=("enchantment",)),),
    "historic permanents": (
        _query(types_all=("artifact",)),
        _query(supertypes_all=("legendary",)),
        _query(subtypes_all=("saga",)),
    ),
    "outlaws": (
        _query(
            subtypes_any=(
                "assassin",
                "mercenary",
                "pirate",
                "rogue",
                "warlock",
            )
        ),
    ),
    "planeswalkers": (_query(types_all=("planeswalker",)),),
    "snow lands": (
        _query(types_all=("land",), supertypes_all=("snow",)),
    ),
    "tokens": (_query(token=True),),
    **{
        quality: (_query(subtypes_all=(subtype,)),)
        for quality, subtype in _SUBTYPE_QUALITIES.items()
    },
}
_AFFINITY_LINE = re.compile(
    r"^Affinity for (?P<quality>[A-Za-z][A-Za-z ]*)\.?$",
    re.IGNORECASE,
)


def _query_is_characteristic_only(query: ObjectQuerySpec) -> bool:
    return bool(
        not query.zones
        and query.owner is None
        and query.controller is None
        and not query.keywords_all
        and query.tapped is None
        and not query.include_phased_out
        and query.known_to_actor is None
        and query.exclude_ref is None
        and query.state_predicate is None
    )


@dataclass(frozen=True, slots=True)
class AffinitySpec:
    """One source-pinned Affinity quality evaluated over effective objects."""

    quality: str
    queries_any: tuple[ObjectQuerySpec, ...]
    schema_version: int = 2

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise CastingPaymentKeywordError(
                "Unsupported typed Affinity schema version"
            )
        if type(self.quality) is not str:
            raise CastingPaymentKeywordError("Affinity quality must be a string")
        quality = self.quality.strip().casefold()
        object.__setattr__(self, "quality", quality)
        expected = _AFFINITY_QUERIES.get(quality)
        if expected is None:
            raise CastingPaymentKeywordError(
                "Affinity quality is outside the closed grammar"
            )
        if not isinstance(self.queries_any, tuple) or not self.queries_any:
            raise CastingPaymentKeywordError(
                "Affinity requires one or more typed object queries"
            )
        if any(
            not isinstance(query, ObjectQuerySpec)
            or not _query_is_characteristic_only(query)
            for query in self.queries_any
        ):
            raise CastingPaymentKeywordError(
                "Affinity queries must contain only effective characteristics"
            )
        if self.queries_any != expected:
            raise CastingPaymentKeywordError(
                "Affinity queries do not match the canonical quality"
            )

    @classmethod
    def for_quality(cls, quality: str) -> "AffinitySpec":
        normalized = quality.strip().casefold()
        queries = _AFFINITY_QUERIES.get(normalized)
        if queries is None:
            raise CastingPaymentKeywordError(
                "Affinity quality is outside the closed grammar"
            )
        return cls(normalized, queries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "quality": self.quality,
            "queries_any": [query.to_dict() for query in self.queries_any],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AffinitySpec":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "quality",
            "queries_any",
        }:
            raise CastingPaymentKeywordError(
                "Typed Affinity descriptors have a closed schema"
            )
        raw_queries = value["queries_any"]
        if not isinstance(raw_queries, list):
            raise CastingPaymentKeywordError(
                "Affinity queries must be an array"
            )
        try:
            queries = tuple(ObjectQuerySpec.from_dict(item) for item in raw_queries)
        except (ObjectQueryError, TypeError) as exc:
            raise CastingPaymentKeywordError(str(exc)) from exc
        return cls(
            quality=value["quality"],
            queries_any=queries,
            schema_version=value["schema_version"],
        )

    def to_payment_mechanic(self) -> dict[str, Any]:
        return {"kind": AFFINITY_MECHANIC_ID, **self.to_dict()}


def compile_affinity(material_line: str) -> AffinitySpec | None:
    match = _AFFINITY_LINE.fullmatch(material_line.strip())
    if match is None:
        return None
    try:
        return AffinitySpec.for_quality(match.group("quality"))
    except CastingPaymentKeywordError:
        return None


@dataclass(frozen=True, slots=True)
class ImproviseSpec:
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CastingPaymentKeywordError(
                "Unsupported Improvise schema version"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "kind": IMPROVISE_MECHANIC_ID}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImproviseSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "kind",
        }:
            raise CastingPaymentKeywordError(
                "Improvise descriptors have a closed schema"
            )
        if value["kind"] != IMPROVISE_MECHANIC_ID:
            raise CastingPaymentKeywordError("Improvise kind changed")
        return cls(schema_version=value["schema_version"])


@dataclass(frozen=True, slots=True)
class DelveSpec:
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CastingPaymentKeywordError("Unsupported Delve schema version")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "kind": DELVE_MECHANIC_ID}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DelveSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "kind",
        }:
            raise CastingPaymentKeywordError(
                "Delve descriptors have a closed schema"
            )
        if value["kind"] != DELVE_MECHANIC_ID:
            raise CastingPaymentKeywordError("Delve kind changed")
        return cls(schema_version=value["schema_version"])


@dataclass(frozen=True, slots=True)
class DelveCandidate:
    ref: str
    object_id: str
    logical_object_id: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.ref, self.object_id, self.logical_object_id)
        ):
            raise CastingPaymentKeywordError(
                "Delve candidates require stable nonempty identities"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "ref": self.ref,
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DelveCandidate":
        if not isinstance(value, Mapping) or set(value) != {
            "ref",
            "object_id",
            "logical_object_id",
        }:
            raise CastingPaymentKeywordError(
                "Delve candidates have a closed schema"
            )
        return cls(
            ref=value["ref"],
            object_id=value["object_id"],
            logical_object_id=value["logical_object_id"],
        )


@dataclass(frozen=True, slots=True)
class DelvePaymentPlan:
    original_requirements: tuple[tuple[str, int], ...]
    remaining_requirements: tuple[tuple[str, int], ...]
    selected: tuple[DelveCandidate, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CastingPaymentKeywordError(
                "Unsupported Delve payment-plan version"
            )
        original = canonical_mana_requirements(dict(self.original_requirements))
        remaining = canonical_mana_requirements(dict(self.remaining_requirements))
        object.__setattr__(self, "original_requirements", tuple(original.items()))
        object.__setattr__(self, "remaining_requirements", tuple(remaining.items()))
        if not isinstance(self.selected, tuple) or any(
            not isinstance(candidate, DelveCandidate)
            for candidate in self.selected
        ):
            raise CastingPaymentKeywordError(
                "Delve payment selections must be typed candidates"
            )
        if len({candidate.ref for candidate in self.selected}) != len(self.selected):
            raise CastingPaymentKeywordError(
                "Delve cannot select one graveyard card more than once"
            )
        if any(original[symbol] != remaining[symbol] for symbol in "WUBRGC"):
            raise CastingPaymentKeywordError("Delve pays only generic mana")
        if original["GENERIC"] - remaining["GENERIC"] != len(self.selected):
            raise CastingPaymentKeywordError(
                "Delve selection count does not match its generic payment"
            )

    @property
    def remaining_dict(self) -> dict[str, int]:
        return dict(self.remaining_requirements)

    @property
    def selected_refs(self) -> tuple[str, ...]:
        return tuple(candidate.ref for candidate in self.selected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "original_requirements": dict(self.original_requirements),
            "remaining_requirements": dict(self.remaining_requirements),
            "selected": [candidate.to_dict() for candidate in self.selected],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DelvePaymentPlan":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "original_requirements",
            "remaining_requirements",
            "selected",
        }:
            raise CastingPaymentKeywordError(
                "Delve payment plans have a closed schema"
            )
        original = value["original_requirements"]
        remaining = value["remaining_requirements"]
        selected = value["selected"]
        if (
            not isinstance(original, Mapping)
            or not isinstance(remaining, Mapping)
            or not isinstance(selected, list)
        ):
            raise CastingPaymentKeywordError("Delve payment-plan fields are malformed")
        return cls(
            original_requirements=tuple(original.items()),
            remaining_requirements=tuple(remaining.items()),
            selected=tuple(DelveCandidate.from_dict(item) for item in selected),
            schema_version=value["schema_version"],
        )


__all__ = [
    "AFFINITY_MECHANIC_ID",
    "AffinitySpec",
    "CastingPaymentKeywordError",
    "DELVE_MECHANIC_ID",
    "DelveCandidate",
    "DelvePaymentPlan",
    "DelveSpec",
    "IMPROVISE_MECHANIC_ID",
    "ImproviseSpec",
    "compile_affinity",
]
