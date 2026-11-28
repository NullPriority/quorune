"""Compile the complete targeted end-step scry and source-counter Mill choice."""

from . import regex_working_set as re

from ..counter_names import normalized_counter_name
from ..rules.event_optional_draw_shapes import controller_end_step_condition
from ..rules.source_counter_mill_choice import END_STEP_MILL_MECHANIC, SourceCounterMillChoice, opponent_schema
from ..rules.source_references import SourceReferenceSpec


def source_counter_mill_template(binding, body, *, card_name):
    if ((binding.event.value, binding.active_zone, binding.variant) != ("step.begin", "battlefield", "your end step")
            or binding.event_condition != controller_end_step_condition() or body != binding.body):
        return None
    match = re.fullmatch(
        r"put an? (?P<counter>[a-z]+) counter on (?P<source>.+?) and scry (?P<scry>[1-9])\. "
        r"Then target opponent may have you draw a card\. If that player doesn['’]t, you mill X cards, "
        r"where X is the number of (?P<repeated_counter>[a-z]+) counters on (?P<repeated>.+?), "
        r"and that player loses life equal to the total mana value of those cards\.",
        " ".join(body.split()), re.I,
    )
    if match is None or not card_name:
        return None
    reference = SourceReferenceSpec(card_name)
    counter = normalized_counter_name(match["counter"])
    if (counter != normalized_counter_name(match["repeated_counter"])
            or not all(reference.matches(match[name]) for name in ("source", "repeated"))):
        return None
    spec = SourceCounterMillChoice(counter, int(match["scry"]))
    return ("target-opponent-draw-or-source-counter-mill-v1", spec.effects(), opponent_schema(),
            (END_STEP_MILL_MECHANIC, "cr-115-targets", "cr-122-counters"))
