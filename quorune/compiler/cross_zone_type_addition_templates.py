from __future__ import annotations

"""Closed source text for static type additions across game zones."""

from . import regex_working_set as re

from ..creature_subtypes import canonical_creature_subtype
from ..entry_designations import (
    ENTRY_DESIGNATION_CAPABILITY, SELF_CHOSEN_SUBTYPE_CAPABILITY, SELF_CHOSEN_SUBTYPE_CONTINUOUS_HANDLER_ID,
)
from ..off_battlefield_creatures import (
    OFF_BATTLEFIELD_SELF_CREATURE_CAPABILITY,
    OFF_BATTLEFIELD_SELF_CREATURE_HANDLER,
    OffBattlefieldSelfCreatureSpec,
)
from ..rules.source_references import SourceReferenceSpec
from ..semantic_runtime.cross_zone_type_additions import (
    CONTROLLED_CHOSEN_CREATURE_SUBTYPE_CAPABILITY, CONTROLLED_CHOSEN_CREATURE_SUBTYPE_HANDLER,
    CROSS_ZONE_CHOSEN_CREATURE_TYPE_CAPABILITY, CROSS_ZONE_CHOSEN_CREATURE_TYPE_HANDLER,
    CROSS_ZONE_ALL_CREATURE_TYPES_CAPABILITY,
    CROSS_ZONE_TYPE_ADDITION_CAPABILITY, CROSS_ZONE_TYPE_ADDITION_HANDLER, CrossZoneTypeAdditionSpec,
)


from .chosen_basic_land_entry import chosen_basic_land_handler


_CREATURES = re.compile(
    r"Creatures you control are (?P<type>artifacts|enchantments|the chosen type) in addition to their other types\. "
    r"The same is true for creature spells you control and creature cards you own that aren't on the battlefield\.",
    re.IGNORECASE,
)
_ALL_CREATURE_SUBTYPES = re.compile(
    r"Creatures you control are every creature type\. "
    r"The same is true for creature spells you control and creature cards you own that aren't on the battlefield\.",
    re.IGNORECASE,
)
_NONLAND = re.compile(
    r"Nonland permanents you control are (?P<type>artifacts|enchantments) in addition to their other types\. "
    r"The same is true for permanent spells you control and nonland permanent cards you own that aren't on the battlefield\.",
    re.IGNORECASE,
)
_PERMANENTS_AND_SPELLS = re.compile(
    r"Nonland permanents you control and permanent spells you control are (?P<type>artifacts|enchantments) "
    r"in addition to their other types\.",
    re.IGNORECASE,
)


def cross_zone_type_addition_handler(text):
    material = " ".join(text.strip().replace("’", "'").split())
    if chosen_basic_land := chosen_basic_land_handler(material):
        return chosen_basic_land
    if material.casefold() == "this creature is the chosen type in addition to its other types.":
        return ("static-self-chosen-subtype-v1", {
            "handler_id": SELF_CHOSEN_SUBTYPE_CONTINUOUS_HANDLER_ID, "schema_version": 1,
            "event": "characteristics.evaluate", "requires_complete_card_program": True,
        }, (ENTRY_DESIGNATION_CAPABILITY, SELF_CHOSEN_SUBTYPE_CAPABILITY))
    if material.casefold() == "each creature you control is the chosen type in addition to its other types.":
        return ("static-controlled-chosen-creature-subtype-v1", {
            "handler_id": CONTROLLED_CHOSEN_CREATURE_SUBTYPE_HANDLER, "schema_version": 1,
            "event": "characteristics.evaluate", "requires_complete_card_program": True,
        }, (ENTRY_DESIGNATION_CAPABILITY, CONTROLLED_CHOSEN_CREATURE_SUBTYPE_CAPABILITY))
    if _ALL_CREATURE_SUBTYPES.fullmatch(material) is not None:
        spec = CrossZoneTypeAdditionSpec(None, "creatures", True, schema_version=2)
        return ("static-cross-zone-all-creature-types-v2", {
            "handler_id": CROSS_ZONE_TYPE_ADDITION_HANDLER, "schema_version": 1,
            "event": "characteristics.evaluate", "requires_complete_card_program": True,
            "addition": spec.to_dict(),
        }, (CROSS_ZONE_ALL_CREATURE_TYPES_CAPABILITY,))
    for grammar, receiver, owned in (
        (_CREATURES, "creatures", True),
        (_NONLAND, "nonland_permanents", True),
        (_PERMANENTS_AND_SPELLS, "nonland_permanents", False),
    ):
        match = grammar.fullmatch(material)
        if match is not None:
            added_type = match["type"].casefold()
            if added_type == "the chosen type":
                return ("static-cross-zone-chosen-creature-type-v1", {
                    "handler_id": CROSS_ZONE_CHOSEN_CREATURE_TYPE_HANDLER, "schema_version": 1,
                    "event": "characteristics.evaluate", "requires_complete_card_program": True,
                }, CROSS_ZONE_CHOSEN_CREATURE_TYPE_CAPABILITY)
            spec = CrossZoneTypeAdditionSpec(added_type.removesuffix("s"), receiver, owned)
            return ("static-cross-zone-type-addition-v1", {
                "handler_id": CROSS_ZONE_TYPE_ADDITION_HANDLER, "schema_version": 1,
                "event": "characteristics.evaluate", "requires_complete_card_program": True,
                "addition": spec.to_dict(),
            }, CROSS_ZONE_TYPE_ADDITION_CAPABILITY)
    return None


def off_battlefield_self_creature(text: str, *, source_name: str) -> OffBattlefieldSelfCreatureSpec | None:
    subject = SourceReferenceSpec(source_name).regex_pattern
    match = re.fullmatch(
        rf"As long as (?:This card|{subject}) isn't on the battlefield, it's "
        r"(?:a|an) (?P<power>0|[1-9][0-9]?)/(?P<toughness>0|[1-9][0-9]?) "
        r"(?P<subtype>.+) creature in addition to its other types\.",
        " ".join(text.replace("’", "'").split()),
        re.IGNORECASE,
    )
    if match is None:
        return None
    subtype = canonical_creature_subtype(match["subtype"])
    if subtype is None:
        return None
    return OffBattlefieldSelfCreatureSpec(int(match["power"]), int(match["toughness"]), subtype)


def off_battlefield_self_creature_handler(text: str, *, source_name: str):
    spec = off_battlefield_self_creature(text, source_name=source_name)
    if spec is None:
        return None
    return (
        "off-battlefield-self-creature-characteristics-v1",
        {
            "handler_id": OFF_BATTLEFIELD_SELF_CREATURE_HANDLER,
            "schema_version": 1,
            "event": "continuous",
            "requires_complete_card_program": True,
            "characteristics": spec.to_dict(),
        },
        OFF_BATTLEFIELD_SELF_CREATURE_CAPABILITY,
    )
