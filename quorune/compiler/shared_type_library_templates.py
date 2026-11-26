"""Full-consumption private top-look/shared-type optional reveal composition."""
from . import regex_working_set as re
from ..rules.shared_type_library_model import (
    SharedTypeLibraryResult, SHARED_TYPE_LIBRARY_MECHANIC, SHARED_TYPE_PLAY_MECHANIC, SHARED_TYPE_PLAY_OPERATION,
    SHARED_TYPE_REVEAL_CAST_VERSION,
)

_LOOK = re.compile(
    r"You may look at the top card of your library\. "
    r"If it shares a creature type with this creature, you may reveal it\. "
    r"If you do, (?P<result>.+)", re.I,
)


_REVEAL_AND_CAST = (
    "reveal the top card of your library. if it's a creature card that shares a creature type "
    "with a creature you control, you may cast it without paying its mana cost. "
    "if you don't cast it, put it on the bottom of your library."
)


def shared_type_library_result_template(binding, body, *, compile_result):
    if binding.variant != "your upkeep" or binding.active_zone != "battlefield":
        return None
    text = " ".join(body.split())
    if text.casefold().replace("’", "'") == _REVEAL_AND_CAST:
        result = SharedTypeLibraryResult(
            ({"op": SHARED_TYPE_PLAY_OPERATION},), (SHARED_TYPE_PLAY_MECHANIC,), SHARED_TYPE_REVEAL_CAST_VERSION,
        )
        return ("public-top-shared-creature-cast-or-bottom-v1", (result.effect(),), None,
                (SHARED_TYPE_LIBRARY_MECHANIC, SHARED_TYPE_PLAY_MECHANIC))
    matched = _LOOK.fullmatch(text)
    if matched is None:
        return None
    if matched["result"].casefold() == "you may play that card without paying its mana cost.":
        mechanics = (SHARED_TYPE_PLAY_MECHANIC,)
        result = SharedTypeLibraryResult(({"op": SHARED_TYPE_PLAY_OPERATION},), mechanics)
        return ("private-top-shared-type-play-v1", (result.effect(),), None,
                (*mechanics, SHARED_TYPE_LIBRARY_MECHANIC))
    template, effects, targets, mechanics = compile_result(matched["result"])
    if template is None or targets is not None:
        return None
    try:
        result = SharedTypeLibraryResult(tuple(effects), tuple(mechanics))
    except (ValueError, TypeError, KeyError):
        return None
    return ("private-top-shared-type-result-v1", (result.effect(),), None,
            (*(m for m in mechanics if m != "closed-effect-program"), SHARED_TYPE_LIBRARY_MECHANIC))
