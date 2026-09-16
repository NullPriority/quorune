from __future__ import annotations

"""Typed parent/child scopes for shared layer-6 component applicability."""

from dataclasses import dataclass
from typing import Any, Mapping

from .ability_fragment_primitives import AbilityFragmentError


@dataclass(frozen=True, slots=True)
class StaticComponentApplicabilitySpec:
    """One closed source-state predicate for a static component scope."""

    kind: str
    designation: str
    minimum: int

    def __post_init__(self) -> None:
        if self.kind != "source_numeric_designation_at_least":
            raise AbilityFragmentError(
                "Static component applicability kind is unsupported"
            )
        if self.designation != "class_level":
            raise AbilityFragmentError(
                "Static component applicability designation is unsupported"
            )
        if type(self.minimum) is not int or self.minimum < 1:
            raise AbilityFragmentError(
                "Static component applicability minimum must be positive"
            )

    def applies(self, source_designations: Mapping[str, Any] | None) -> bool:
        if not isinstance(source_designations, Mapping):
            return False
        value = source_designations.get(self.designation)
        return bool(type(value) is int and value >= self.minimum)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "designation": self.designation,
            "minimum": self.minimum,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "StaticComponentApplicabilitySpec":
        if not isinstance(value, Mapping) or set(value) != {
            "kind",
            "designation",
            "minimum",
        }:
            raise AbilityFragmentError(
                "Static component applicability uses a closed schema"
            )
        return cls(
            kind=value["kind"],
            designation=value["designation"],
            minimum=value["minimum"],
        )


@dataclass(frozen=True, slots=True)
class StaticComponentScopeSpec:
    """Keywords and child components supplied by one static component."""

    parent_semantic_key: str
    child_semantic_keys: tuple[str, ...]
    keywords: tuple[str, ...]
    applicability: StaticComponentApplicabilitySpec | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AbilityFragmentError(
                "Unsupported static-component-scope fragment schema version"
            )
        if (
            type(self.parent_semantic_key) is not str
            or not self.parent_semantic_key.strip()
            or self.parent_semantic_key != self.parent_semantic_key.strip()
        ):
            raise AbilityFragmentError(
                "Static component scopes require one canonical parent key"
            )
        if (
            not isinstance(self.child_semantic_keys, tuple)
            or any(
                type(key) is not str
                or not key.strip()
                or key != key.strip()
                for key in self.child_semantic_keys
            )
            or len(set(self.child_semantic_keys))
            != len(self.child_semantic_keys)
            or self.child_semantic_keys != tuple(sorted(self.child_semantic_keys))
            or self.parent_semantic_key in self.child_semantic_keys
        ):
            raise AbilityFragmentError(
                "Static component scopes require unique canonical child keys"
            )
        if (
            not isinstance(self.keywords, tuple)
            or any(
                type(keyword) is not str
                or not keyword.strip()
                or keyword != keyword.strip()
                for keyword in self.keywords
            )
            or len({keyword.casefold() for keyword in self.keywords})
            != len(self.keywords)
            or self.keywords
            != tuple(sorted(self.keywords, key=str.casefold))
        ):
            raise AbilityFragmentError(
                "Static keyword scopes require unique canonical keywords"
            )
        if self.applicability is not None and not isinstance(
            self.applicability, StaticComponentApplicabilitySpec
        ):
            raise AbilityFragmentError(
                "Static component applicability must be typed"
            )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "parent_semantic_key": self.parent_semantic_key,
            "child_semantic_keys": list(self.child_semantic_keys),
            "keywords": list(self.keywords),
        }
        if self.applicability is not None:
            result["applicability"] = self.applicability.to_dict()
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StaticComponentScopeSpec":
        expected = {
            "schema_version",
            "parent_semantic_key",
            "child_semantic_keys",
            "keywords",
        }
        if not isinstance(value, Mapping) or set(value) not in {
            frozenset(expected),
            frozenset((*expected, "applicability")),
        }:
            raise AbilityFragmentError(
                "Static component scopes have a closed schema"
            )
        if not isinstance(value["keywords"], list) or not isinstance(
            value["child_semantic_keys"], list
        ):
            raise AbilityFragmentError(
                "Static component scope keys and keywords must be arrays"
            )
        return cls(
            schema_version=value["schema_version"],
            parent_semantic_key=value["parent_semantic_key"],
            child_semantic_keys=tuple(value["child_semantic_keys"]),
            keywords=tuple(value["keywords"]),
            applicability=(
                StaticComponentApplicabilitySpec.from_dict(
                    value["applicability"]
                )
                if "applicability" in value
                else None
            ),
        )


__all__ = [
    "StaticComponentApplicabilitySpec",
    "StaticComponentScopeSpec",
]
