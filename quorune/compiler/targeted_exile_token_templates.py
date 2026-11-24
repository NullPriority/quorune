"""Lower one bound token definition over actual creature exile results."""

from . import regex_working_set as re

from ..rules.targeted_exile_token_results import (
    TARGET_EXILE_OPERATION, TARGET_EXILE_TOKEN_MECHANIC, TargetedExileTokenFollowup,
)
from .announced_target_sets import announced_target_set_effect_template
from .any_number_target_sets import any_number_target_set_template


_RESULT = re.compile(
    r"Exile (?P<count>X|any number of) target creatures\. "
    r"For each creature exiled this way, its controller creates (?P<token>an? [^.]+)\.",
    re.IGNORECASE,
)


def targeted_exile_token_template(text: str, *, compile_literal, cast_x_available: bool):
    match = _RESULT.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    prefix = f"Exile {match['count']} target creatures."
    if match["count"].casefold() == "x":
        if not cast_x_available:
            return None
        exile = announced_target_set_effect_template(prefix)
    else:
        exile = any_number_target_set_template(prefix)
    if exile is None or exile[1][0]["op"] != TARGET_EXILE_OPERATION:
        return None
    _id, tokens, targets, mechanics = compile_literal(f"Create {match['token']}.")
    if targets is not None or len(tokens) != 1:
        return None
    try:
        followup = TargetedExileTokenFollowup(tokens[0])
    except (TypeError, ValueError):
        return None
    return (
        f"{exile[0]}-controller-tokens-v1",
        ({**exile[1][0], "followup": followup.to_dict()},),
        exile[2],
        tuple(dict.fromkeys((*exile[3], *mechanics, TARGET_EXILE_TOKEN_MECHANIC))),
    )
