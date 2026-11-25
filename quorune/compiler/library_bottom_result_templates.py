"""Lower the exact bottom-card actions and their complete linked results."""

from ..library_bottom_result_model import (
    LIBRARY_BOTTOM_RESULT_MECHANIC, LibraryBottomResultKind, LibraryBottomResultSpec,
)
from ..replacement.immutable import FrozenMap
from ..rules.source_references import matches_source_power_reference
from . import regex_working_set as re
from .token_templates import fixed_token_creation_effect_template


_TARGET_TOKEN = re.compile(
    r"Target player puts the bottom card of their library into their graveyard\. "
    r"If it's a creature card, you (?P<token>create [^!?]+)", re.IGNORECASE,
)
_SOURCE_POWER_ENTRY = re.compile(
    r"Put the bottom card of your library into your graveyard\. "
    r"If it's a creature card with power less than or equal to (?P<source>[^.!?]+)'s power, "
    r"put it onto the battlefield\.", re.IGNORECASE,
)


def library_bottom_creature_result_template(text, *, card_name):
    material = " ".join(text.split()).replace("\u2019", "'")
    target = _TARGET_TOKEN.fullmatch(material)
    if target is not None:
        token = fixed_token_creation_effect_template(target["token"])
        if token is None or token.template_id != "create-fixed-creature-token-v2" or token.target_schema is not None:
            return None
        spec = LibraryBottomResultSpec(LibraryBottomResultKind.TARGET_PLAYER_CREATURE_TOKEN, FrozenMap(token.effect))
        mechanics = (LIBRARY_BOTTOM_RESULT_MECHANIC, "cr-115-targets")
    else:
        entry = _SOURCE_POWER_ENTRY.fullmatch(material)
        if entry is None or not matches_source_power_reference(entry["source"], card_name):
            return None
        spec = LibraryBottomResultSpec(LibraryBottomResultKind.CONTROLLER_SOURCE_POWER_ENTRY)
        mechanics = (LIBRARY_BOTTOM_RESULT_MECHANIC,)
    return (
        f"library-bottom-{spec.kind.value}-v1", (spec.to_effect(),), spec.target_schema(), mechanics,
    )
