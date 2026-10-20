from __future__ import annotations

"""Immutable declaration-dependent target cardinality, without target selection."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

ANNOUNCED_TARGET_SET_CAPABILITY = "target.cardinality.mana_announced_x"


class TargetCardinalityError(ValueError):
    """A declared target count or its source descriptor is malformed."""


@dataclass(frozen=True, slots=True)
class AnnouncedTargetCardinality:
    selection: str = "exact"
    parameter: str = "x"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise TargetCardinalityError("Unsupported announced target-count schema")
        if type(self.parameter) is not str or self.parameter != "x":
            raise TargetCardinalityError("Target cardinality requires the declared X parameter")
        if type(self.selection) is not str or self.selection not in {"exact", "up_to"}:
            raise TargetCardinalityError("Target cardinality requires exact or up-to selection")

    def bounds(self, declared_x: Any) -> tuple[int, int]:
        value = 0 if declared_x is None else declared_x
        if type(value) is not int or value < 0:
            raise TargetCardinalityError("Target-count X must be a nonnegative integer")
        return (value if self.selection == "exact" else 0), value

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "parameter": self.parameter,
            "selection": self.selection}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AnnouncedTargetCardinality:
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "parameter", "selection"}:
            raise TargetCardinalityError("Announced target cardinality has unknown or missing fields")
        return cls(**dict(value))


def bind_announced_target_schema(schema: Mapping[str, Any], declared_x: Any) -> dict[str, Any]:
    """Bind a source count to concrete legacy bounds before legality queries.

    This does not choose X, constrain its mana price, enumerate targets or grant
    a capability. Legacy schemas retain their exact shape and caller ownership.
    """

    if not isinstance(schema, Mapping):
        raise TargetCardinalityError("A target schema must be an object")
    result = deepcopy(dict(schema))
    if "count_parameter" not in result:
        return result
    if set(result).intersection({"count", "min", "max", "minimum", "maximum", "up_to", "groups", "modes"}):
        raise TargetCardinalityError("Announced target count cannot mix fixed, grouped or modal cardinality")
    spec = AnnouncedTargetCardinality.from_dict(result.pop("count_parameter"))
    _minimum, maximum = spec.bounds(declared_x)
    result["count" if spec.selection == "exact" else "up_to"] = maximum
    return result


def link_announced_public_bounds(schema: Mapping[str, Any], parameter: Mapping[str, Any]) -> dict[str, Any]:
    """Attach a dependent form link after concrete legality projection."""
    result = deepcopy(dict(schema))
    spec = AnnouncedTargetCardinality.from_dict(parameter)
    field = "count_from" if spec.selection == "exact" else "maximum_from"
    for group in result.get("groups", ()):
        group[field] = "x"
    result[field] = "x"
    return result
