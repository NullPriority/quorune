from __future__ import annotations

"""Typed characteristics of the permanent used for a fixed sacrifice price."""

from collections.abc import Mapping
from dataclasses import dataclass
from .type_subtype_vocabulary import CARD_SUPERTYPES, CARD_TYPE_SUBTYPES


PAID_SACRIFICE_CONDITION_CAPABILITY = "resolution.effect.paid_sacrifice_characteristic"
PAID_SACRIFICE_CONDITION_MECHANIC = "fixed-paid-sacrifice-characteristic"
PAID_SACRIFICE_CONDITION_VERSION = 14
PAID_SACRIFICE_CHARACTERISTICS_CONTEXT = "paid_sacrifice_characteristics"
PAID_SACRIFICE_CONDITION_VALUE = "paid_sacrifice_characteristic_value"
_SUBJECTS = frozenset({"artifact", "creature", "permanent"})


def paid_sacrifice_trait_field(trait: str) -> str:
    if trait in CARD_TYPE_SUBTYPES:
        return "types"
    if trait in CARD_SUPERTYPES:
        return "supertypes"
    if any(trait in subtypes for subtypes in CARD_TYPE_SUBTYPES.values()):
        return "subtypes"
    raise ValueError("Paid sacrifice condition requires a represented type, subtype, or supertype")


@dataclass(frozen=True, slots=True)
class PaidSacrificeCharacteristicCondition:
    subject: str
    trait: str
    kind: str = "paid_sacrifice_characteristic"

    def __post_init__(self) -> None:
        if type(self.subject) is not str or self.subject not in _SUBJECTS or type(self.trait) is not str:
            raise ValueError("Paid sacrifice condition requires a closed subject and characteristic")
        if self.kind != "paid_sacrifice_characteristic":
            raise ValueError("Paid sacrifice condition kind is unsupported")
        paid_sacrifice_trait_field(self.trait)

    def to_dict(self) -> dict:
        return {"schema_version": 1, "kind": self.kind, "subject": self.subject, "trait": self.trait}

    @classmethod
    def from_dict(cls, value: Mapping) -> PaidSacrificeCharacteristicCondition:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "kind", "subject", "trait"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Paid sacrifice condition has a closed versioned schema")
        return cls(value["subject"], value["trait"], value["kind"])

    def reference(self) -> dict:
        return {"kind": PAID_SACRIFICE_CONDITION_VALUE, "schema_version": 1, "condition": self.to_dict()}

    def matches(self, paid: PaidSacrificeCharacteristics) -> bool:
        return self.trait in getattr(paid, paid_sacrifice_trait_field(self.trait))


@dataclass(frozen=True, slots=True)
class PaidSacrificeCharacteristics:
    logical_object_id: str
    types: tuple[str, ...]
    subtypes: tuple[str, ...]
    supertypes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.logical_object_id) is not str or not self.logical_object_id:
            raise ValueError("Paid sacrifice characteristics require a battlefield incarnation")
        for values in (self.types, self.subtypes, self.supertypes):
            if (type(values) is not tuple
                    or any(type(value) is not str or not value or value != value.strip().casefold() for value in values)
                    or tuple(sorted(set(values))) != values):
                raise ValueError("Paid sacrifice characteristics are not canonical")

    def to_dict(self) -> dict:
        return {"schema_version": 1, "logical_object_id": self.logical_object_id,
                "types": list(self.types), "subtypes": list(self.subtypes), "supertypes": list(self.supertypes)}

    @classmethod
    def from_dict(cls, value: Mapping) -> PaidSacrificeCharacteristics:
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "logical_object_id", "types", "subtypes", "supertypes"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or any(not isinstance(value[key], (tuple, list)) for key in ("types", "subtypes", "supertypes"))):
            raise ValueError("Paid sacrifice characteristics are unavailable or malformed")
        return cls(value["logical_object_id"], tuple(value["types"]), tuple(value["subtypes"]), tuple(value["supertypes"]))


def paid_sacrifice_condition_from_reference(value: Mapping) -> PaidSacrificeCharacteristicCondition:
    if (not isinstance(value, Mapping) or set(value) != {"kind", "schema_version", "condition"}
            or value["kind"] != PAID_SACRIFICE_CONDITION_VALUE
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("Paid sacrifice characteristic reference is malformed")
    return PaidSacrificeCharacteristicCondition.from_dict(value["condition"])


def validate_paid_sacrifice_instruction(effect: Mapping):
    from .resolution_conditions import RESOLUTION_CONDITION_OPERATION, resolution_result_is_fixed

    fields = {"op", "schema_version", "player", "condition", "cast_fact", "effects", "mechanic_ids",
              "prefix_mechanic_ids"}
    alternative = "otherwise_effects" in effect
    if alternative:
        fields.update({"otherwise_effects", "otherwise_mechanic_ids"})
    if (set(effect) != fields or effect["op"] != RESOLUTION_CONDITION_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != PAID_SACRIFICE_CONDITION_VERSION
            or type(effect["player"]) is not str or not effect["player"]):
        raise ValueError("Paid sacrifice instruction has a closed schema")
    condition = PaidSacrificeCharacteristicCondition.from_dict(effect["condition"])
    if type(effect["cast_fact"]) is not bool and effect["cast_fact"] != condition.reference():
        raise ValueError("Paid sacrifice instruction lost its bound characteristic")
    prefix = effect["prefix_mechanic_ids"]
    groups = ((effect["effects"], effect["mechanic_ids"]),)
    if alternative:
        groups += ((effect["otherwise_effects"], effect["otherwise_mechanic_ids"]),)
    for ids in (prefix, *(ids for _, ids in groups)):
        if (not isinstance(ids, (list, tuple)) or any(type(x) is not str or not x for x in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("Paid sacrifice instruction requires closed component mechanics")
    for children, ids in groups:
        if (not isinstance(children, (list, tuple)) or len(children) != 1 or not ids
                or not isinstance(children[0], Mapping) or children[0].get("op") == RESOLUTION_CONDITION_OPERATION
                or not resolution_result_is_fixed(children, ids)):
            raise ValueError("Paid sacrifice instruction requires one independently represented fixed result")
    if alternative and (prefix or any(children[0].get("op") != "draw" for children, _ in groups)):
        raise ValueError("Paid sacrifice alternative requires two untargeted draw results")
    return condition, tuple(effect["effects"])
