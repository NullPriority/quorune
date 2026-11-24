"""Full-consumption private top-look/shared-type optional reveal composition."""
from . import regex_working_set as re
from ..rules.shared_type_library_model import (
    SharedTypeLibraryResult, SHARED_TYPE_LIBRARY_MECHANIC, SHARED_TYPE_PLAY_MECHANIC, SHARED_TYPE_PLAY_OPERATION,
)

_LOOK = re.compile(
    r"You may look at the top card of your library\. "
    r"If it shares a creature type with this creature, you may reveal it\. "
    r"If you do, (?P<result>.+)", re.I,
)


def shared_type_library_result_template(binding, body, *, compile_result):
    if binding.variant != "your upkeep" or binding.active_zone != "battlefield":
        return None
    matched = _LOOK.fullmatch(" ".join(body.split()))
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
