"""The single-creature turn prohibition and its same-target kicked bonus."""

from . import regex_working_set as re
from ..rules.targeted_turn_target_prohibition import targeted_turn_prohibition_spell


def targeted_turn_prohibition_template(text):
    if not re.fullmatch(
        r"Target creature can['’]t be the target of spells or abilities your opponents control this turn\. "
        r"If this spell was kicked, that creature gets \+4/\+4 until end of turn\.",
        text.strip(), re.IGNORECASE,
    ):
        return None
    effects, schema, mechanics = targeted_turn_prohibition_spell()
    return "fixed-targeted-turn-prohibition-and-kicked-result-v1", effects, schema, mechanics
