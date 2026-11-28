"""Lower the exact printed clockwise or counterclockwise control instruction."""

from . import regex_working_set as re
from ..rules.directional_control import MECHANIC, TEMPLATE, directional_control_choice
from ..rules.source_references import SourceReferenceSpec


_DIRECTION = re.compile(
    r"Choose left or right\. Each player gains control of all nonland permanents other than "
    r"(?P<source>.+?) controlled by the next player in the chosen direction\.",
    re.IGNORECASE,
)


def directional_control_template(text, *, source_name):
    match = _DIRECTION.fullmatch(" ".join(text.split()))
    if match is None or not SourceReferenceSpec(source_name).matches(match["source"]):
        return None
    return TEMPLATE, (directional_control_choice(),), None, (MECHANIC, "cr-611-continuous-effects")
