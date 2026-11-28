"""Compile full-table private choices with a public high/low damage and draw result."""

from . import regex_working_set as re
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..rules.source_references import SourceReferenceSpec
from ..rules.secret_number_choices import MECHANIC, OPERATION

_INSTRUCTION = re.compile(
    r"Each player secretly chooses a number 0 or greater, then all players reveal those numbers "
    r"simultaneously and determine the highest and lowest numbers revealed this way\. "
    r"(?P<source>.+?) deals damage equal to the highest number to each player who chose that number\. "
    r"Each player who didn['’]t choose the lowest number discards their hand, then draws "
    rf"(?P<count>{FIXED_COUNT_PATTERN}) cards?\.",
    re.IGNORECASE,
)


def secret_number_extremes_template(text, *, card_name, source_is_permanent):
    if source_is_permanent is not False:
        return None
    match = _INSTRUCTION.fullmatch(" ".join(text.split()))
    if match is None or not (match["source"].casefold() == "this spell"
                             or SourceReferenceSpec(card_name).matches(match["source"])):
        return None
    count = fixed_number(match["count"])
    if type(count) is not int or count < 1:
        return None
    return ("secret-number-extremes-v1", ({
        "op": OPERATION, "schema_version": 1, "player": "$controller", "count": count,
    },), None, (MECHANIC,))
