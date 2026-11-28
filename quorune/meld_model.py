"""Closed printed pair and result declarations for CR 712.5."""

from collections.abc import Mapping
from dataclasses import dataclass


MELD_PAIR_FIELD = "meld_pair"
MELD_CAPABILITY = "zone.composition.meld_pair"
MELD_MECHANIC = "fixed-meld-pair"
MELD_TEMPLATE = "fixed-owned-controlled-meld-pair-v1"
MELD_OPERATION = "linked_exile_return"

# CR 712.5 supplies pair data. Every pair uses the same physical owners.
_MELD_PAIRS = (
    (("Midnight Scavengers", "Graf Rats"), "Chittering Host"),
    (("Hanweir Garrison", "Hanweir Battlements"), "Hanweir, the Writhing Township"),
    (("Bruna, the Fading Light", "Gisela, the Broken Blade"), "Brisela, Voice of Nightmares"),
    (("Phyrexian Dragon Engine", "Mishra, Claimed by Gix"), "Mishra, Lost to Phyrexia"),
    (("The Mightstone and Weakstone", "Urza, Lord Protector"), "Urza, Planeswalker"),
    (("Argoth, Sanctum of Nature", "Titania, Voice of Gaea"), "Titania, Gaea Incarnate"),
    (("Fang, Fearless l’Cie", "Vanille, Cheerful l’Cie"), "Ragnarok, Divine Deliverance"),
)


def _name_key(name):
    return name.replace("’", "'").casefold()


def _pair_names(source, partner, combined):
    requested = {_name_key(source), _name_key(partner)}
    for fronts, result in _MELD_PAIRS:
        if requested == {_name_key(front) for front in fronts} and _name_key(combined) == _name_key(result):
            ordered = fronts if _name_key(source) == _name_key(fronts[0]) else fronts[::-1]
            return (*ordered, result)
    return None


@dataclass(frozen=True, slots=True)
class MeldPairSpec:
    source_name: str
    partner_name: str
    result_name: str

    def __post_init__(self):
        names = (self.source_name, self.partner_name, self.result_name)
        if any(type(value) is not str or not value for value in names) or _pair_names(*names) != names:
            raise ValueError("Meld requires one actual printed pair and combined face")

    @classmethod
    def from_names(cls, source, partner, result):
        names = _pair_names(source, partner, result)
        return cls(*names) if names else None

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "source", "partner", "result"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Meld pair records have a closed schema")
        return cls(value["source"], value["partner"], value["result"])

    def to_dict(self):
        return {"schema_version": 1, "source": self.source_name, "partner": self.partner_name, "result": self.result_name}


def uses_meld_pair(effect):
    return isinstance(effect, Mapping) and MELD_PAIR_FIELD in effect


def meld_pair_effects(spec):
    return (
        {"op": "choose_objects", MELD_PAIR_FIELD: spec.to_dict()},
        {"op": MELD_OPERATION, "phase": "meld", MELD_PAIR_FIELD: spec.to_dict()},
    )


def meld_exile_effect(spec, *, object_id, logical_object_id):
    return {"op": MELD_OPERATION, "phase": "exile", MELD_PAIR_FIELD: spec.to_dict(),
            "partner": {"object_id": object_id, "logical_object_id": logical_object_id}}


def validate_meld_pair_instruction(effect):
    phase = effect.get("phase") if isinstance(effect, Mapping) else None
    fields = {"op", "phase", MELD_PAIR_FIELD} | ({"partner"} if phase == "exile" else set())
    if (not uses_meld_pair(effect) or set(effect) - {"_replacement_selections"} != fields
            or effect["op"] != MELD_OPERATION or phase not in {"exile", "meld"}):
        raise ValueError("Meld requires the exact original exile or arrival instruction")
    if phase == "exile":
        partner = effect["partner"]
        if (not isinstance(partner, Mapping) or set(partner) != {"object_id", "logical_object_id"}
                or any(type(value) is not str or not value for value in partner.values())):
            raise ValueError("Meld exile requires the selected original creature incarnation")
    return MeldPairSpec.from_dict(effect[MELD_PAIR_FIELD])


def related_meld_records(catalog, records):
    for record in records:
        yield record
        if record.layout != "meld":
            continue
        for fronts, result in _MELD_PAIRS:
            if _name_key(record.name) not in {_name_key(front) for front in fronts}:
                continue
            try:
                companion = catalog.lookup(next(front for front in fronts if _name_key(front) != _name_key(record.name)), fuzzy=False)
                combined = catalog.lookup(result, fuzzy=False)
            except KeyError:
                continue
            if companion.layout == combined.layout == "meld":
                yield companion
                yield combined
