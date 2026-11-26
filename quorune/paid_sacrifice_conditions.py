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
ACTIVATION_PAID_SUBTYPE_CAPABILITY = "resolution.effect.activation_paid_creature_subtype"
ACTIVATION_PAID_LEGENDARY_AMASS_CAPABILITY = "resolution.effect.activation_paid_legendary_amass"
ACTIVATION_PAID_THRULL_COUNTERS_CAPABILITY = "resolution.effect.activation_paid_thrull_counters"
ACTIVATION_PAID_SUSPECTED_DRAW_CAPABILITY = "resolution.effect.activation_paid_suspected_draw"
ACTIVATION_PAID_SUSPECTED_REFERENCE_VERSION = 3
PAID_SUSPECTED_CONDITION_KIND = "paid_sacrifice_suspected"
ACTIVATION_PAID_SUBTYPE_REFERENCE_VERSION = 2
_SUBJECTS = frozenset({"artifact", "creature", "permanent"})
_ACTIVATION_SUBTYPE_COUNTER = {
    "op": "place_counters", "card": "$source.zone_object", "counter": "+1/+1", "amount": 1, "source": "$source",
}



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
        if self.kind == PAID_SUSPECTED_CONDITION_KIND:
            if (self.subject, self.trait) != ("creature", "suspected"):
                raise ValueError("The paid designation requires one suspected creature")
            return
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

    def reference(self, *, activation=False) -> dict:
        version = 1
        if activation:
            version = (ACTIVATION_PAID_SUSPECTED_REFERENCE_VERSION if self.kind == PAID_SUSPECTED_CONDITION_KIND
                       else ACTIVATION_PAID_SUBTYPE_REFERENCE_VERSION)
        return {"kind": PAID_SACRIFICE_CONDITION_VALUE, "schema_version": version, "condition": self.to_dict()}

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
            or type(value["schema_version"]) is not int
            or value["schema_version"] not in (1, ACTIVATION_PAID_SUBTYPE_REFERENCE_VERSION,
                                                ACTIVATION_PAID_SUSPECTED_REFERENCE_VERSION)):
        raise ValueError("Paid sacrifice characteristic reference is malformed")
    condition = PaidSacrificeCharacteristicCondition.from_dict(value["condition"])
    if ((value["schema_version"] == ACTIVATION_PAID_SUSPECTED_REFERENCE_VERSION)
            != (condition.kind == PAID_SUSPECTED_CONDITION_KIND)):
        raise ValueError("Paid suspected designation requires its own activation reference")
    return condition


def is_activation_paid_creature_reference(value):
    return (isinstance(value, Mapping) and value.get("kind") == PAID_SACRIFICE_CONDITION_VALUE
            and value.get("schema_version") in (ACTIVATION_PAID_SUBTYPE_REFERENCE_VERSION,
                                                ACTIVATION_PAID_SUSPECTED_REFERENCE_VERSION))


def activation_paid_subtype_result_is_closed(condition, reference, prefix, children):
    if (reference != condition.reference(activation=True) or condition.subject != "creature"
            or condition.trait not in CARD_TYPE_SUBTYPES["creature"] or len(prefix) != 1 or len(children) != 1):
        return False
    from .util import stable_json

    grant = prefix[0]
    keywords = grant.get("keywords")
    if not isinstance(keywords, (list, tuple)) or tuple(keywords) not in (("Flying",), ("Indestructible",)):
        return False
    expected_grant = {
        "op": "apply_source_characteristics_until_end_of_turn", "card": "$source.zone_object",
        "set_card_types": None, "set_subtypes": None, "set_colors": None, "base_power": None,
        "base_toughness": None, "power": 0, "toughness": 0, "keywords": list(keywords),
    }
    return stable_json(dict(grant)) == stable_json(expected_grant) and _activation_subtype_counter_is_closed(children)


def _activation_subtype_counter_is_closed(children, *, counter="+1/+1"):
    return (len(children) == 1 and isinstance(children[0], Mapping)
            and type(children[0].get("amount")) is int
            and children[0] == {**_ACTIVATION_SUBTYPE_COUNTER, "counter": counter})


def activation_paid_thrull_counters_result_is_closed(condition, reference, prefix, children):
    return (
        condition.subject == "creature" and condition.trait == "thrull"
        and reference == condition.reference(activation=True)
        and len(prefix) == 1 and isinstance(prefix[0], Mapping) and type(prefix[0].get("amount")) is int
        and prefix[0] == {"op": "remove_counters", "card": "$source.zone_object",
                          "counter": "-2/-2", "amount": 1, "source": "$source"}
        and _activation_subtype_counter_is_closed(children, counter="+1/+0")
    )


def _fixed_private_draw_is_closed(children, count):
    return (len(children) == 1 and isinstance(children[0], Mapping)
            and type(children[0].get("count")) is int and children[0].get("private") is True
            and children[0] == {"op": "draw", "player": "$controller", "count": count, "private": True})


def activation_paid_suspected_draw_is_closed(condition, reference, prefix, children, otherwise, target_schema):
    return (condition.kind == PAID_SUSPECTED_CONDITION_KIND
            and reference == condition.reference(activation=True)
            and target_schema is None and not prefix
            and _fixed_private_draw_is_closed(children, 2) and _fixed_private_draw_is_closed(otherwise, 1))


def _activation_paid_condition_child_is_closed(condition, children):
    if condition.kind == PAID_SUSPECTED_CONDITION_KIND:
        return _fixed_private_draw_is_closed(children, 1) or _fixed_private_draw_is_closed(children, 2)
    if condition.trait == "legendary":
        return _activation_legendary_amass_is_closed(children)
    return (_activation_subtype_counter_is_closed(children)
            or condition.trait == "thrull" and _activation_subtype_counter_is_closed(children, counter="+1/+0"))


def _activation_legendary_amass_is_closed(children):
    return (len(children) == 1 and isinstance(children[0], Mapping)
            and type(children[0].get("amount")) is int
            and children[0] == {"op": "amass", "subtype": "Orc", "amount": 2})


def activation_paid_legendary_amass_result_is_closed(condition, reference, prefix, children, target_schema):
    return (
        condition.subject == "creature" and condition.trait == "legendary"
        and reference == condition.reference(activation=True)
        and isinstance(target_schema, Mapping) and type(target_schema.get("count")) is int
        and target_schema == {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}
        and len(prefix) == 1 and isinstance(prefix[0], Mapping) and type(prefix[0].get("amount")) is int
        and prefix[0] == {"op": "lose_life", "player": "$target.0", "amount": 1}
        and _activation_legendary_amass_is_closed(children)
    )


def validate_paid_sacrifice_instruction(effect: Mapping):
    from .resolution_conditions import RESOLUTION_CONDITION_OPERATION, resolution_result_is_fixed
    from .paid_activation_card_types import uses_paid_type_condition, validate_paid_type_instruction

    if uses_paid_type_condition(effect):
        return validate_paid_type_instruction(effect)

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
    if type(effect["cast_fact"]) is not bool and paid_sacrifice_condition_from_reference(effect["cast_fact"]) != condition:
        raise ValueError("Paid sacrifice instruction lost its bound characteristic")
    activation = is_activation_paid_creature_reference(effect["cast_fact"])
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
                or not (_activation_paid_condition_child_is_closed(condition, children) if activation
                        else resolution_result_is_fixed(children, ids))):
            raise ValueError("Paid sacrifice instruction requires one independently represented fixed result")
    if alternative and (prefix or any(children[0].get("op") != "draw" for children, _ in groups)):
        raise ValueError("Paid sacrifice alternative requires two untargeted draw results")
    return condition, tuple(effect["effects"])
