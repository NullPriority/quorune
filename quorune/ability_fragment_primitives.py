from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


PARTNER_WITH_FRAGMENT_HANDLER_ID = "ability.static.partner-with.v1"
TOXIC_ABILITY_FRAGMENT_KIND = "toxic"


class AbilityFragmentError(ValueError):
    """A typed executable ability fragment is malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class ToxicSpec:
    """One executable instance of the printed CR 702.164 Toxic ability."""

    value: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AbilityFragmentError(
                "Unsupported Toxic fragment schema version"
            )
        if type(self.value) is not int or self.value <= 0:
            raise AbilityFragmentError(
                "Toxic values must be positive integers"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ToxicSpec":
        expected = {"schema_version", "value"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise AbilityFragmentError(
                "Toxic fragments have a closed schema"
            )
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class PartnerWithSpec:
    """One compiler-certified named Partner with relationship."""

    partner_name: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AbilityFragmentError(
                "Unsupported Partner with fragment schema version"
            )
        if (
            type(self.partner_name) is not str
            or not self.partner_name.strip()
            or self.partner_name != self.partner_name.strip()
            or "\n" in self.partner_name
            or '"' in self.partner_name
        ):
            raise AbilityFragmentError(
                "Partner with fragments require one exact card name"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "partner_name": self.partner_name,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PartnerWithSpec":
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version",
            "partner_name",
        }:
            raise AbilityFragmentError(
                "Partner with fragments have a closed schema"
            )
        return cls(**dict(value))


__all__ = [
    "AbilityFragmentError",
    "PARTNER_WITH_FRAGMENT_HANDLER_ID",
    "PartnerWithSpec",
    "TOXIC_ABILITY_FRAGMENT_KIND",
    "ToxicSpec",
]


def normalized_protection_terms(
    values: Iterable[str],
    *,
    field_name: str,
    allowed: frozenset[str],
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise AbilityFragmentError(
            f"Protection source predicate {field_name} must be an array"
        )
    normalized = tuple(sorted(str(value).casefold() for value in values))
    if (
        any(type(value) is not str or not value for value in values)
        or len(set(normalized)) != len(normalized)
        or any(value not in allowed for value in normalized)
    ):
        raise AbilityFragmentError(
            f"Protection source predicate {field_name} is unsupported"
        )
    return normalized
