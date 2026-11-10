from __future__ import annotations

"""Closed source text for static card-type additions across game zones."""

import re

from ..semantic_runtime.cross_zone_type_additions import (
    CROSS_ZONE_TYPE_ADDITION_CAPABILITY, CROSS_ZONE_TYPE_ADDITION_HANDLER, CrossZoneTypeAdditionSpec,
)


_CREATURES = re.compile(
    r"Creatures you control are (?P<type>artifacts|enchantments) in addition to their other types\. "
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
    for grammar, receiver, owned in (
        (_CREATURES, "creatures", True),
        (_NONLAND, "nonland_permanents", True),
        (_PERMANENTS_AND_SPELLS, "nonland_permanents", False),
    ):
        match = grammar.fullmatch(material)
        if match is not None:
            spec = CrossZoneTypeAdditionSpec(match["type"].casefold().removesuffix("s"), receiver, owned)
            return ("static-cross-zone-type-addition-v1", {
                "handler_id": CROSS_ZONE_TYPE_ADDITION_HANDLER, "schema_version": 1,
                "event": "characteristics.evaluate", "requires_complete_card_program": True,
                "addition": spec.to_dict(),
            }, CROSS_ZONE_TYPE_ADDITION_CAPABILITY)
    return None
