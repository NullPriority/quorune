"""Typed fixed one-X Harmonize price and paid-original departure."""

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .flashback import paid_stack_exile_replacement
from .replacement.immutable import FrozenMap, thaw_value
from .spree_model import MANA_KEYS
from .util import stable_json


CAPABILITY = "casting.harmonize.fixed_announced_x"
HANDLER_ID = "casting.harmonize.fixed-announced-x.v1"
MECHANIC = "harmonize"
EVENT = "cast.cost"
FINGERPRINT = "harmonize_fingerprint"
TAP_PAYMENT = "harmonize_tap_payment"
ANNOTATION = "cast_via_harmonize"
BASE_OPTION_ID = "harmonize"
DEPENDENCIES = (
    CAPABILITY, "casting.additional_cost.fixed_tap", "quantity_expression.declared_effect_amount",
    "zone.change.destination_replacement",
)


@dataclass(frozen=True, slots=True)
class HarmonizeSpec:
    mana: FrozenMap
    schema_version: int = 1

    def __post_init__(self):
        if (
            type(self.schema_version) is not int or self.schema_version != 1
            or not isinstance(self.mana, Mapping) or set(self.mana) != set(MANA_KEYS)
            or any(type(value) is not int or value < 0 for value in self.mana.values())
        ):
            raise ValueError("Harmonize requires an exact fixed mana vector and one announced X")
        object.__setattr__(self, "mana", FrozenMap(self.mana))

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "mana"}:
            raise ValueError("Harmonize cost fields are closed")
        return cls(value["mana"], value["schema_version"])

    def to_dict(self):
        return {"schema_version": self.schema_version, "mana": thaw_value(self.mana)}

    @property
    def fingerprint(self):
        return sha256(stable_json(self.to_dict()).encode("utf-8")).hexdigest()

    def option(self, x):
        if type(x) is not int or x < 0:
            raise ValueError("Harmonize requires a nonnegative announced X")
        requirements = thaw_value(self.mana)
        requirements["GENERIC"] += x
        return {
            "id": BASE_OPTION_ID, "kind": "alternate", "label": "Harmonize",
            "requirements": requirements, FINGERPRINT: self.fingerprint, "source_zone": "graveyard",
        }


def harmonize_handler_descriptor(spec):
    return {
        "handler_id": HANDLER_ID, "schema_version": 1, "event": EVENT,
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True, "harmonize": spec.to_dict(),
    }


@dataclass(frozen=True, slots=True)
class HarmonizeTapPayment:
    object_id: str
    ref: str
    logical_object_id: str
    power: int

    def __post_init__(self):
        if (
            any(type(value) is not str or not value for value in (self.object_id, self.ref, self.logical_object_id))
            or type(self.power) is not int
        ):
            raise ValueError("Harmonize tap payment requires the exact incarnation and signed power")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"object_id", "ref", "logical_object_id", "power"}:
            raise ValueError("Harmonize tap payment has malformed fields")
        return cls(**value)

    def to_dict(self):
        return {"object_id": self.object_id, "ref": self.ref,
                "logical_object_id": self.logical_object_id, "power": self.power}

    @property
    def reduction(self):
        return max(self.power, 0)

    def matches(self, card):
        return (card.object_id, card.ref, card.logical_object_id) == (self.object_id, self.ref, self.logical_object_id)


def harmonize_generic_reduction(option):
    payment = option.get(TAP_PAYMENT)
    return HarmonizeTapPayment.from_dict(payment).reduction if payment is not None else 0


def harmonize_subject_replacements(cards, object_ids):
    return tuple(
        replacement for object_id in object_ids
        if (replacement := paid_stack_exile_replacement(
            cards.get(object_id), annotation=ANNOTATION, rule="harmonize",
            description="exile the harmonized spell instead",
        )) is not None
    )
