from __future__ import annotations

"""Lower one complete optional resolving cast and its destination rider."""

import re

from ..rules.graveyard_card_targets import targeted_graveyard_cast_schema
from ..targeted_graveyard_cast_model import GRAVEYARD_CAST_MECHANIC, GRAVEYARD_CAST_OPERATION

_INSTRUCTION = re.compile(
    r"You may cast target instant or sorcery card from (?P<owner>a|an opponent['’]s) graveyard "
    r"without paying its mana cost\."
    r"(?P<rider> If that spell would be put into (?:a|their) graveyard, exile it instead\.)?",
    re.IGNORECASE,
)


def targeted_graveyard_cast_template(text):
    match = _INSTRUCTION.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    relation = "any" if match["owner"].casefold() == "a" else "opponent"
    rider = match["rider"]
    if relation == "any" and rider is not None and "their graveyard" in rider.casefold():
        return None
    return "targeted-resolving-graveyard-cast-v1", ({
        "op": GRAVEYARD_CAST_OPERATION, "player": "$controller", "card": "$target.0",
        "owner_relation": relation, "exile_on_graveyard": rider is not None,
    },), targeted_graveyard_cast_schema(relation), (GRAVEYARD_CAST_MECHANIC, "cr-115-targets")
