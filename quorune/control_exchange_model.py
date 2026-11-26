"""The closed two-permanent exchange, excluding other exchange actions."""

from collections.abc import Mapping
from enum import StrEnum

from .entry_control_exchange_model import ENTRY_EXCHANGE_CAPABILITY
from .target_forms import (
    SHARED_PERMANENT_TYPE_PAIR_CAPABILITY, SHARED_PERMANENT_TYPE_PAIR_FIELD,
    SharedPermanentTypePair, SharedPermanentTypeScope,
)


PERMANENT_CONTROL_EXCHANGE_OPERATION = "exchange_permanent_control"
PERMANENT_CONTROL_EXCHANGE_CAPABILITY = "continuous.control.permanent_exchange"
PERMANENT_CONTROL_EXCHANGE_MECHANIC = "fixed-permanent-control-exchange"
PERMANENT_CONTROL_EXCHANGE_DEPENDENCIES = (
    PERMANENT_CONTROL_EXCHANGE_CAPABILITY,
    "continuous.control.fixed_resolution",
    "target.revalidate_resolution",
)


class PermanentControlExchange(StrEnum):
    CREATURE = "creature"
    ARTIFACT = "artifact"
    ENCHANTMENT = "enchantment"
    LAND = "land"
    CONTROLLER_AND_OPPONENT_LAND = "controller_and_opponent_land"
    SOURCE_AND_NONLAND = "source_and_nonland"
    SHARED_PERMANENT_TYPE = "shared_permanent_type"
    SHARED_ARTIFACT_OR_CREATURE_TYPE = "shared_artifact_or_creature_type"
    ENTRY_SHARED_TYPE = "entry_shared_type"

    @property
    def type_pair(self):
        if self is self.SHARED_PERMANENT_TYPE:
            return SharedPermanentTypePair("first", "second", SharedPermanentTypeScope.PERMANENT)
        if self is self.SHARED_ARTIFACT_OR_CREATURE_TYPE:
            return SharedPermanentTypePair("first", "second", SharedPermanentTypeScope.ARTIFACT_OR_CREATURE)
        return None

    @property
    def capability_dependencies(self):
        if self is self.ENTRY_SHARED_TYPE:
            return (*PERMANENT_CONTROL_EXCHANGE_DEPENDENCIES, ENTRY_EXCHANGE_CAPABILITY)
        if self.type_pair is not None:
            return (*PERMANENT_CONTROL_EXCHANGE_DEPENDENCIES, SHARED_PERMANENT_TYPE_PAIR_CAPABILITY)
        return PERMANENT_CONTROL_EXCHANGE_DEPENDENCIES

    def target_schema(self):
        permanent = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}
        if self is self.ENTRY_SHARED_TYPE:
            return permanent
        if self is self.SOURCE_AND_NONLAND:
            return {**permanent, "types_none": ["land"]}
        if self is self.CONTROLLER_AND_OPPONENT_LAND:
            land = {**permanent, "types_any": ["land"]}
            return {
                "groups": [
                    {**land, "id": "own_land", "controller_relation": "you"},
                    {**land, "id": "opposing_land", "controller_relation": "opponent"},
                ],
            }
        pair = self.type_pair
        if pair is not None:
            left = {**permanent, "id": pair.left}
            if pair.scope is SharedPermanentTypeScope.ARTIFACT_OR_CREATURE:
                left["types_any"] = sorted(pair.scope.types)
            return {
                "groups": [left, {**permanent, "id": pair.right}],
                "globally_distinct": True,
                SHARED_PERMANENT_TYPE_PAIR_FIELD: pair.to_dict(),
            }
        return {**permanent, "count": 2, "types_any": [self.value]}

    def to_effect(self):
        left, right = self.references
        return {
            "op": PERMANENT_CONTROL_EXCHANGE_OPERATION, "schema_version": 1,
            "scope": self.value, "left": left, "right": right,
        }

    @property
    def references(self):
        if self is self.ENTRY_SHARED_TYPE:
            return "$event.entry_object", "$target.0"
        if self is self.SOURCE_AND_NONLAND:
            return "$source.zone_object", "$target.0"
        return "$target.0", "$target.1"


def decode_permanent_control_exchange(effect: Mapping, *, resolved=False):
    fields = {"op", "schema_version", "scope", "left", "right"}
    if resolved and isinstance(effect, Mapping) and "reason" in effect:
        fields.add("reason")
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect["op"] != PERMANENT_CONTROL_EXCHANGE_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1):
        raise ValueError("Permanent control exchange has invalid fields or version")
    if "reason" in fields and (type(effect["reason"]) is not str or not effect["reason"]):
        raise ValueError("Permanent control exchange requires a nonempty reason")
    scope = PermanentControlExchange(effect["scope"])
    references = (effect["left"], effect["right"])
    if resolved:
        if any(ref is not None and (type(ref) is not str or not ref or ref.startswith("$")) for ref in references):
            raise ValueError("Permanent control exchange requires resolved public references")
    elif references != scope.references:
        raise ValueError("Permanent control exchange changed the original subject roles")
    return scope, references
