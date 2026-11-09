from __future__ import annotations

"""Lower a complete fixed graveyard exile, copy and resolving-cast result."""

import re

from ..rules.graveyard_card_targets import graveyard_copy_scope_schema
from ..rules.graveyard_copy_cast_model import GRAVEYARD_COPY_MECHANIC, GRAVEYARD_COPY_OPERATION

_ONE = re.compile(
    r"Exile target instant or sorcery card from (?P<graveyard>a|an opponent['’]s) graveyard\. "
    r"Copy that card(?P<three> three times)?\. You may cast (?P<result>the copy without paying its mana cost"
    r"|the copies without paying their mana costs)\.",
    re.IGNORECASE,
)
_PAIRED = re.compile(
    r"Exile target instant or sorcery card from your graveyard and target instant or sorcery card "
    r"from an opponent['’]s graveyard\. Copy those cards\. Cast the copies if able without paying their mana costs\.",
    re.IGNORECASE,
)
_EACH = re.compile(
    r"Exile (?P<subject>target|each) card that['’]s an instant or sorcery from your graveyard\. "
    r"For each card exiled this way, copy it, and you may cast the copy without paying its mana cost\.",
    re.IGNORECASE,
)


def graveyard_copy_cast_template(text):
    if not text.lstrip().casefold().startswith("exile "):
        return None
    normalized = " ".join(text.split())
    copies = 1
    if (match := _ONE.fullmatch(normalized)) is not None:
        copies = 3 if match["three"] is not None else 1
        scope = "any" if match["graveyard"].casefold() == "a" else "opponent"
        if (copies == 3) != match["result"].casefold().startswith("the copies ") or copies == 3 and scope != "any":
            return None
    elif _PAIRED.fullmatch(normalized):
        scope = "paired"
    elif (match := _EACH.fullmatch(normalized)) is not None:
        scope = "own_all" if match["subject"].casefold() == "each" else "own"
    else:
        return None
    target_schema = graveyard_copy_scope_schema(scope)
    effect = {"op": GRAVEYARD_COPY_OPERATION, "player": "$controller", "scope": scope,
              "copies": copies, "cards": "$targets" if target_schema is not None else []}
    return "graveyard-copy-resolving-cast-v1", (effect,), target_schema, (
        GRAVEYARD_COPY_MECHANIC, *(("cr-115-targets",) if target_schema is not None else ()),
    )
