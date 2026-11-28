from __future__ import annotations

"""Lower one complete optional resolving cast and its destination rider."""

from . import regex_working_set as re

from ..rules.graveyard_card_targets import targeted_graveyard_cast_instruction_schema
from ..rules.source_references import matches_source_power_reference
from ..replacement.immutable import thaw_value
from ..rules.target_graveyard_flashback import GRAVEYARD_FLASHBACK_MECHANIC, INSTRUCTION, TARGET_SCHEMA
from ..targeted_graveyard_cast_model import (
    GRAVEYARD_CAST_MECHANIC,
    GRAVEYARD_CAST_OPERATION,
    surveil_graveyard_cast_effects,
)

_INSTRUCTION = re.compile(
    r"You may cast target instant or sorcery card from (?P<owner>a|an opponent['’]s) graveyard "
    r"without paying its mana cost\."
    r"(?P<rider> If that spell would be put into (?:a|their) graveyard, exile it instead\.)?",
    re.IGNORECASE,
)


_SOURCE_POWER_INSTRUCTION = re.compile(
    r"You may cast target instant or sorcery card with mana value less than or equal to (?P<source>.+?)['’]s power "
    r"from your graveyard without paying its mana cost\. If that spell would be put into your graveyard, exile it instead\.",
    re.IGNORECASE,
)


_SURVEIL_AND_CAST = re.compile(
    r"surveil 2\. Then you may cast an instant or sorcery spell with mana value 2 or less "
    r"from your graveyard without paying its mana cost\. "
    r"If that spell would be put into your graveyard, exile it instead\.",
    re.IGNORECASE,
)


def equipped_surveil_graveyard_cast_template(binding, body):
    if ((binding.event.value, binding.variant, binding.active_zone)
            == ("damage.dealt", "equipped_creature_combat_damage_a_player", "battlefield")
            and body == binding.body and _SURVEIL_AND_CAST.fullmatch(" ".join(body.split()))):
        return ("equipped-surveil-graveyard-cast-v1", surveil_graveyard_cast_effects(),
                None, ("surveil", GRAVEYARD_CAST_MECHANIC))
    return None


def relative_graveyard_cast_template(text, *, card_name):
    if not text.casefold().startswith("you may cast target "):
        return None
    match = _SOURCE_POWER_INSTRUCTION.fullmatch(" ".join(text.split()))
    if match is None or not matches_source_power_reference(match["source"], card_name):
        return None
    return _cast_result("you", True)


def targeted_graveyard_cast_template(text):
    match = _INSTRUCTION.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    relation = "any" if match["owner"].casefold() == "a" else "opponent"
    rider = match["rider"]
    if relation == "any" and rider is not None and "their graveyard" in rider.casefold():
        return None
    return _cast_result(relation, rider is not None)


_FLASHBACK_GRANT = re.compile(
    r"Target instant or sorcery card in your graveyard gains flashback until end of turn\. "
    r"The flashback cost is equal to its mana cost\."
    r"(?: \(You may cast that card from your graveyard for its flashback cost\. Then exile it\.\))?",
    re.IGNORECASE,
)


def targeted_graveyard_flashback_template(text):
    if _FLASHBACK_GRANT.fullmatch(" ".join(text.split())) is None:
        return None
    return ("target-graveyard-printed-price-flashback-v1", (thaw_value(INSTRUCTION),), thaw_value(TARGET_SCHEMA),
            (GRAVEYARD_FLASHBACK_MECHANIC, "cr-115-targets"))


def _cast_result(relation, exile_on_graveyard):
    return "targeted-resolving-graveyard-cast-v1", ({
        "op": GRAVEYARD_CAST_OPERATION, "player": "$controller", "card": "$target.0",
        "owner_relation": relation, "exile_on_graveyard": exile_on_graveyard,
    },), targeted_graveyard_cast_instruction_schema(relation), (GRAVEYARD_CAST_MECHANIC, "cr-115-targets")
