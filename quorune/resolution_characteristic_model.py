from __future__ import annotations

"""Closed values for one fixed multi-layer resolution characteristic result."""

from dataclasses import dataclass
from typing import Any, Mapping

from .continuous_effects import ContinuousOperation, Layer
from .creature_subtypes import CREATURE_SUBTYPES, canonical_creature_subtype
from .keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from .object_predicate import ObjectQuerySpec


PERMANENT_CHARACTERISTIC_MECHANIC = "fixed-source-characteristics-until-end-of-turn"
PERMANENT_CHARACTERISTIC_CAPABILITY = (
    "continuous.resolution.fixed_source_characteristics_until_end_of_turn"
)
CHARACTERISTIC_OPERATION = "apply_source_characteristics_until_end_of_turn"
# CR 205.3g-k/q, pinned with the repository's 2026-08-07 rules index.
# Every non-retaining animation in this closed model sets Creature alone.
# Remove subtypes correlated with the lost types, not retained creature types.
_LOST_NONCREATURE_SUBTYPES = tuple(sorted("""
Attraction Blood Bobblehead Book Clue Contraption Equipment Food Fortification
Gold Incubator Infinity Junk Lander Map Mutagen Powerstone Spacecraft Stone
Treasure Vehicle Vibranium Aura Background Cartouche Case Class Curse Plan
Role Room Rune Saga Shard Shrine Cave Desert Forest Gate Island Lair Locus Mine
Mountain Plains Planet Power-Plant Sphere Swamp Tower Town Urza's
Ajani Aminatou Angrath Arlinn Ashiok Bahamut Basri Bolas Calix Chandra Comet Dack
Dakkon Daretti Davriel Dellian Dihada Domri Dovin Ellywick Elminster Elspeth
Estrid Freyalise Garruk Gideon Grist Guff Huatli Jace Jared Jaya Jeska Kaito Karn
Kasmina Kaya Kiora Koth Liliana Lolth Lukka Minsc Mordenkainen Nahiri Narset Niko
Nissa Nixilis Oko Quintorius Ral Rowan Saheeli Samut Sarkhan Serra Sivitri Sorin
Szat Tamiyo Tasha Teferi Teyo Tezzeret Tibalt Tyvar Ugin Urza Venser Vivien
Vraska Vronos Will Windgrace Wrenn Xenagos Yanggu Yanling Zariel
Adventure Arcane Lesson Omen Trap Siege
""".split()))


@dataclass(frozen=True, slots=True)
class FixedResolutionCharacteristicsSpec:
    card_types: tuple[str, ...] | None = None
    creature_subtypes: tuple[str, ...] | None = None
    colors: tuple[str, ...] | None = None
    retain_types: bool = False
    retain_creature_subtypes: bool = False
    remove_all_abilities: bool = False
    keywords: tuple[str, ...] = ()
    base_power: int = 0
    base_toughness: int = 0
    schema_version: int = 2

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ValueError("Fixed resolution characteristics require version 2")
        for flag in (self.retain_types, self.retain_creature_subtypes, self.remove_all_abilities):
            if type(flag) is not bool:
                raise ValueError("Characteristic flags must be strict booleans")
        if any(type(value) is not int or value < 0 for value in (self.base_power, self.base_toughness)):
            raise ValueError("Base characteristics must be nonnegative integers")
        if self.card_types is not None and self.card_types not in (("Creature",), ("Artifact", "Creature")):
            raise ValueError("Animation requires a closed creature card-type set")
        if self.card_types is not None and not self.retain_types and self.card_types != ("Creature",):
            raise ValueError("Non-retaining animation replaces card types with Creature")
        if self.creature_subtypes is not None and (
            self.card_types is None or len(set(self.creature_subtypes)) != len(self.creature_subtypes)
            or any(type(value) is not str or canonical_creature_subtype(value) is None or value != canonical_creature_subtype(value).title() for value in self.creature_subtypes)
        ):
            raise ValueError("Animation creature subtypes are not canonical")
        if self.colors is not None and (
            any(type(value) is not str or value not in "WUBRG" for value in self.colors)
            or tuple(color for color in "WUBRG" if color in self.colors) != self.colors
        ):
            raise ValueError("Animation colors are not canonical")
        if self.card_types is None and (self.retain_types or self.retain_creature_subtypes):
            raise ValueError("Base-only effects cannot carry type-retention flags")
        if self.retain_creature_subtypes and not self.retain_types:
            raise ValueError("Retained creature subtypes require retained prior types")
        if len(self.keywords) > 3 or len(set(self.keywords)) != len(self.keywords) or any(
            type(value) is not str or value not in FIXED_CHARACTERISTIC_KEYWORDS for value in self.keywords
        ):
            raise ValueError("Animation keywords are outside the fixed vocabulary")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version":self.schema_version,
                "card_types":list(self.card_types) if self.card_types is not None else None,
                "creature_subtypes":list(self.creature_subtypes) if self.creature_subtypes is not None else None,
                "colors":list(self.colors) if self.colors is not None else None,
                "retain_types":self.retain_types,"retain_creature_subtypes":self.retain_creature_subtypes,
                "remove_all_abilities":self.remove_all_abilities,"keywords":list(self.keywords),
                "base_power":self.base_power,"base_toughness":self.base_toughness}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixedResolutionCharacteristicsSpec":
        if not isinstance(value, Mapping) or set(value) != set(cls().to_dict()):
            raise ValueError("Fixed characteristics use a closed versioned schema")
        values = dict(value)
        for field in ("card_types", "creature_subtypes", "colors", "keywords"):
            raw = values[field]
            if raw is not None:
                if not isinstance(raw, (list, tuple)):
                    raise ValueError("Characteristic vocabularies require arrays")
                values[field] = tuple(raw)
        if values["keywords"] is None:
            raise ValueError("Characteristic keywords cannot be null")
        return cls(**values)

    def layer_operations(self) -> tuple[tuple[Layer, str, tuple[ContinuousOperation, ...]], ...]:
        result: list[tuple[Layer, str, tuple[ContinuousOperation, ...]]] = []
        types: list[ContinuousOperation] = []
        if self.card_types is not None:
            types.append(ContinuousOperation("add_types" if self.retain_types else "set_types", self.card_types, field="card_types"))
            if not self.retain_types:
                types.append(ContinuousOperation(
                    "remove_types", _LOST_NONCREATURE_SUBTYPES, field="subtypes"
                ))
            if self.creature_subtypes is not None:
                if not self.retain_creature_subtypes:
                    # A specified creature subtype replaces that subtype set;
                    # retained land/artifact subtype sets are not replaced.
                    types.append(ContinuousOperation("remove_types", tuple(sorted(value.title() for value in CREATURE_SUBTYPES)), field="subtypes"))
                if self.creature_subtypes:
                    types.append(ContinuousOperation("add_types", self.creature_subtypes, field="subtypes"))
        if types:
            result.append((Layer.TYPE,"4",tuple(types)))
        if self.colors is not None:
            operation = ContinuousOperation("set_colors",self.colors) if self.colors else ContinuousOperation("remove_all_colors")
            result.append((Layer.COLOR,"5",(operation,)))
        abilities = ((ContinuousOperation("remove_all_abilities"),) if self.remove_all_abilities else ()) + tuple(
            ContinuousOperation("add_ability",keyword) for keyword in self.keywords)
        if abilities:
            result.append((Layer.ABILITY,"6",abilities))
        result.append((Layer.POWER_TOUGHNESS,"7b",(
            ContinuousOperation("set_power_toughness",(self.base_power,self.base_toughness)),)))
        return tuple(result)


def fixed_resolution_characteristic_instruction(value: Mapping[str, Any]) -> tuple[FixedResolutionCharacteristicsSpec, str | ObjectQuerySpec]:
    expected = {"op","schema_version","characteristics"}
    if not isinstance(value, Mapping) or set(value) not in (expected | {"card"}, expected | {"predicate"}):
        raise ValueError("Fixed characteristic instruction fields are malformed")
    if value["op"] != CHARACTERISTIC_OPERATION or type(value["schema_version"]) is not int or value["schema_version"] != 2:
        raise ValueError("Fixed characteristic instruction identity is malformed")
    spec = FixedResolutionCharacteristicsSpec.from_dict(value["characteristics"])
    if "card" in value:
        if type(value["card"]) is not str or not value["card"]:
            raise ValueError("Fixed characteristic target reference is malformed")
        return spec, value["card"]
    predicate = ObjectQuerySpec.from_dict(value["predicate"])
    if predicate.to_dict() != dict(value["predicate"]):
        raise ValueError("Fixed characteristic predicate is not canonical")
    return spec, predicate
