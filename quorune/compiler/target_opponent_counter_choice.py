"""Compile the source entry that lets the targeted opponent pick a counter recipient."""

from ..counter_placement_targets import opponent_creature_counter_choice_effect
from ..rules.target_opponent_counter_choice import OPPONENT_COUNTER_CHOICE_TARGET


def target_opponent_counter_choice_result(body):
    if (" ".join(body.casefold().split()) !=
            "target opponent puts a deathtouch counter on a creature they control."):
        return None
    return ("target-opponent-untargeted-deathtouch-counter-v1", (opponent_creature_counter_choice_effect(),),
            {**OPPONENT_COUNTER_CHOICE_TARGET}, ("cr-122-counters", "cr-115-targets"))
