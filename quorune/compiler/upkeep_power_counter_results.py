"""Lower the printed power threshold, two counter branches, and life result."""
from . import regex_working_set as re
from ..rules.scheduled_player_shapes import SCHEDULED_PLAYER_MECHANIC
from ..rules.source_references import SourceReferenceSpec
from ..rules.upkeep_power_counter_balance import upkeep_power_counter_effect


def upkeep_power_counter_result(body, *, card_name):
    match = re.fullmatch(
        r"Double the number of \+1/\+1 counters on (?P<source>.+?) if its power is 16 or less\. "
        r"Otherwise, remove all but one \+1/\+1 counter from it, then you gain 1 life "
        r"for each \+1/\+1 counter removed this way\.",
        body, re.IGNORECASE,
    )
    if not match or not card_name or not SourceReferenceSpec(card_name).matches(match["source"]):
        return None
    return ("upkeep-source-power-counter-balance-v1", (upkeep_power_counter_effect(),), None, (SCHEDULED_PLAYER_MECHANIC,))
