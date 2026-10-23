from __future__ import annotations

"""Immutable declaration-dependent target cardinality, without target selection."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

ANNOUNCED_TARGET_SET_CAPABILITY = "target.cardinality.mana_announced_x"
ANY_NUMBER_TARGET_CAPABILITY = "target.cardinality.any_number"
SELECTED_TARGET_COUNT_KIND = "selected_target_count"


class TargetCardinalityError(ValueError):
    """A declared target count or its source descriptor is malformed."""


@dataclass(frozen=True, slots=True)
class AnnouncedTargetCardinality:
    selection: str = "exact"
    parameter: str = "x"
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2}:
            raise TargetCardinalityError("Unsupported announced target-count schema")
        if self.schema_version == 2:
            if type(self.parameter) is not str or self.parameter != "any_number" or self.selection != "up_to":
                raise TargetCardinalityError("Any-number cardinality requires unrestricted optional selection")
        elif type(self.parameter) is not str or self.parameter != "x":
            raise TargetCardinalityError("Target cardinality requires the declared X parameter")
        if type(self.selection) is not str or self.selection not in {"exact", "up_to"}:
            raise TargetCardinalityError("Target cardinality requires exact or up-to selection")

    def bounds(self, declared_x: Any) -> tuple[int, int]:
        if self.parameter == "any_number" and declared_x is None:
            raise TargetCardinalityError("Any-number target bounds require the authoritative object limit")
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
    if spec.parameter == "any_number":
        return result
    field = "count_from" if spec.selection == "exact" else "maximum_from"
    for group in result.get("groups", ()):
        group[field] = "x"
    result[field] = "x"
    return result


def bind_any_number_target_schema(schema: Mapping[str, Any], maximum: int) -> dict[str, Any]:
    """Use the authoritative finite object universe, without choosing targets."""
    parameter = schema.get("count_parameter")
    if parameter is None:
        return deepcopy(dict(schema))
    spec = AnnouncedTargetCardinality.from_dict(parameter)
    if spec.parameter != "any_number":
        return deepcopy(dict(schema))
    return bind_announced_target_schema(schema, maximum)


def selected_target_count_descriptor() -> dict[str, Any]:
    return {"kind": SELECTED_TARGET_COUNT_KIND, "schema_version": 1}


def validate_selected_target_count(value: Mapping[str, Any]) -> None:
    if (not isinstance(value, Mapping) or dict(value) != selected_target_count_descriptor()
            or type(value.get("schema_version")) is not int):
        raise TargetCardinalityError("Selected-target count descriptor is malformed")


def resolve_selected_target_count(value: Mapping[str, Any], item: Any) -> int:
    """Count chosen roles, retaining targets that later become illegal."""
    validate_selected_target_count(value)
    groups = item.context.get("target_groups")
    if not isinstance(groups, Mapping) or not groups:
        raise TargetCardinalityError("Original chosen-target groups are unavailable")
    count = 0
    for refs in groups.values():
        if (not isinstance(refs, (list, tuple))
                or any(type(ref) is not str or not ref for ref in refs)
                or len(set(refs)) != len(refs)):
            raise TargetCardinalityError("Original chosen-target group is malformed")
        count += len(refs)
    return count
