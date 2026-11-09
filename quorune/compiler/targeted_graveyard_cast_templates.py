from __future__ import annotations

"""Lower one complete optional resolving cast and its destination rider."""

import re

from ..rules.graveyard_card_targets import targeted_graveyard_cast_instruction_schema
from ..rules.source_references import matches_source_power_reference
from ..targeted_graveyard_cast_model import GRAVEYARD_CAST_MECHANIC, GRAVEYARD_CAST_OPERATION

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


def _cast_result(relation, exile_on_graveyard):
    return "targeted-resolving-graveyard-cast-v1", ({
        "op": GRAVEYARD_CAST_OPERATION, "player": "$controller", "card": "$target.0",
        "owner_relation": relation, "exile_on_graveyard": exile_on_graveyard,
    },), targeted_graveyard_cast_instruction_schema(relation), (GRAVEYARD_CAST_MECHANIC, "cr-115-targets")
