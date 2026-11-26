"""Lower two complete resolution-time choices without announcing targets."""

from ..rules.permanent_resolution_choices import (
    COUNTER_KEYWORDS_MECHANICS, TYPE_DESTRUCTION_MECHANICS,
    controlled_counter_keywords_choice, permanent_type_destruction_choice,
)


def permanent_resolution_choice_template(text):
    normalized = " ".join(text.casefold().split())
    if normalized == (
        "put a +1/+1 counter on a creature you control. "
        "it gains vigilance and trample until end of turn."
    ):
        return "fixed-controlled-counter-keywords-v1", (controlled_counter_keywords_choice(),), None, COUNTER_KEYWORDS_MECHANICS
    if normalized == "choose artifact or enchantment. destroy all permanents of the chosen type.":
        return "fixed-chosen-permanent-type-destruction-v1", (permanent_type_destruction_choice(),), None, TYPE_DESTRUCTION_MECHANICS
    return None
