"""Descriptor-owned capability requirements for qualified destruction sets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from operator import gt, lt
from typing import Any

from ..affected_permanents import AffectedPermanentSetError, AffectedPermanentSetSpec
from ..object_predicate import ObjectQuerySpec
from ..object_query import ObjectQueryResult

QUALIFIED_DESTRUCTION_CAPABILITY='permanent.destroy.qualified_fixed_set'


TARGET_RELATIVE_POWER_DESTRUCTION_KIND = "target_relative_power_destruction"
TARGET_RELATIVE_POWER_DESTRUCTION_CAPABILITY = "permanent.destroy.target_relative_power_set"
TARGET_RELATIVE_POWER_DESTRUCTION_DEPENDENCIES = (
    TARGET_RELATIVE_POWER_DESTRUCTION_CAPABILITY,
    "permanent.destroy.fixed_set",
    "target.revalidate_resolution",
)


class RelativePowerDestructionScope(str, Enum):
    GREATER = "greater"
    LESS_THAN_CONTROLLED = "less_than_controlled"


@dataclass(frozen=True, slots=True, init=False)
class RelativePowerDestructionSetSpec(AffectedPermanentSetSpec):
    scope: RelativePowerDestructionScope
    target_ref: str

    def __init__(self, scope: RelativePowerDestructionScope, target_ref: str):
        if not isinstance(scope, RelativePowerDestructionScope) or type(target_ref) is not str or not target_ref:
            raise AffectedPermanentSetError("Relative-power destruction requires one typed comparison and target")
        AffectedPermanentSetSpec.__init__(self, query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)))
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "target_ref", target_ref)

    @property
    def target_must_be_controlled(self) -> bool:
        return self.scope is RelativePowerDestructionScope.LESS_THAN_CONTROLLED

    def target_schema(self) -> dict[str, Any]:
        schema = {
            "zones": ["battlefield"], "categories": ["permanent"],
            "count": 1, "types_any": ["creature"],
        }
        if self.target_must_be_controlled:
            schema["controller_relation"] = "you"
        return schema

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection_kind": TARGET_RELATIVE_POWER_DESTRUCTION_KIND,
            "schema_version": 1, "scope": self.scope.value, "target_ref": self.target_ref,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RelativePowerDestructionSetSpec:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"selection_kind", "schema_version", "scope", "target_ref"}
            or value["selection_kind"] != TARGET_RELATIVE_POWER_DESTRUCTION_KIND
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
        ):
            raise AffectedPermanentSetError("Relative-power destruction has a closed versioned selection")
        return cls(RelativePowerDestructionScope(value["scope"]), value["target_ref"])


def is_relative_power_destruction_set(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("selection_kind") == TARGET_RELATIVE_POWER_DESTRUCTION_KIND


def relative_power_destruction_instruction(
    effect: Mapping[str, Any], *, resolved: bool = False,
) -> RelativePowerDestructionSetSpec:
    fields = set(effect)
    if resolved:
        fields -= {"reason", "_replacement_selections"}
    if (fields != {"op", "source", "set"} or effect["op"] != "destroy_all"
            or not is_relative_power_destruction_set(effect["set"])):
        raise ValueError("Relative-power destruction requires its exact simultaneous instruction")
    spec = RelativePowerDestructionSetSpec.from_dict(effect["set"])
    if (resolved and spec.target_ref.startswith("$")
            or not resolved and (spec.target_ref != "$target.0" or effect["source"] != "$source")):
        raise ValueError("Relative-power destruction requires the resolving creature target")
    return spec


def select_relative_power_destruction_rows(
    rows: tuple[ObjectQueryResult, ...], spec: RelativePowerDestructionSetSpec, *, actor: str,
) -> tuple[ObjectQueryResult, ...]:
    anchor = next((row for row in rows if row.ref == spec.target_ref), None)
    if anchor is None or (spec.target_must_be_controlled and anchor.controller != actor):
        raise AffectedPermanentSetError("Relative-power destruction requires its current legal creature anchor")
    if any(type(row.effective_power) is not int for row in rows):
        raise AffectedPermanentSetError("Relative-power destruction requires exact current creature power")
    comparison = lt if spec.target_must_be_controlled else gt
    return tuple(row for row in rows if comparison(row.effective_power, anchor.effective_power))


SOURCE_INCARNATION_EXCLUSION_FIELD = 'source_incarnation_exclusion_version'
SOURCE_INCARNATION_EXCLUDING_SET = AffectedPermanentSetSpec(
    query=ObjectQuerySpec(zones=('battlefield',)),
    exclude_source=True,
)


def source_incarnation_excluding_destruction(value, *, resolved=False):
    """Validate the entire fixed battlefield set and its original source binding."""
    fields = {'op', 'source', 'set', SOURCE_INCARNATION_EXCLUSION_FIELD}
    actual = set(value)
    if resolved:
        actual -= {'reason', '_replacement_selections'}
    version = value.get(SOURCE_INCARNATION_EXCLUSION_FIELD)
    if actual != fields or value.get('op') != 'destroy_all' or type(version) is not int or version != 1:
        raise ValueError('Source-incarnation destruction requires its exact fixed-set instruction')
    spec = AffectedPermanentSetSpec.from_dict(value['set'])
    if spec != SOURCE_INCARNATION_EXCLUDING_SET:
        raise ValueError('Source-incarnation destruction requires all battlefield permanents')
    source = value['source']
    if not resolved and source != '$source.zone_object':
        raise ValueError('Source-incarnation destruction requires the original battlefield object')
    if resolved and source is None:
        return replace(spec, exclude_source=False)
    if type(source) is not str or not source:
        raise ValueError('Source-incarnation destruction requires a resolved public reference')
    return spec


def qualified_destruction_capabilities(query):
    from ..object_predicate import object_query_capabilities
    advanced=(query.numeric_characteristic is not None or query.commander is not None
        or bool(query.colors_none or query.excluded_supertypes or query.any_of)
        or 'aura' in query.excluded_subtypes
        or bool(set(query.subtypes_all)&{'aura','equipment'}))
    return tuple(sorted({*((QUALIFIED_DESTRUCTION_CAPABILITY,) if advanced else ()),
        *(('state_query.commander.designation',) if query.commander is not None else ()),
        *object_query_capabilities(query)}))


def qualified_destruction_payload_dependencies(value):
    if value.get('op')!='destroy_all':return ()
    if SOURCE_INCARNATION_EXCLUSION_FIELD in value:
        source_incarnation_excluding_destruction(value)
        return ('permanent.destroy.fixed_set',)
    if is_relative_power_destruction_set(value.get("set")):
        relative_power_destruction_instruction(value)
        return TARGET_RELATIVE_POWER_DESTRUCTION_DEPENDENCIES
    return qualified_destruction_capabilities(AffectedPermanentSetSpec.from_dict(value['set']).query)
