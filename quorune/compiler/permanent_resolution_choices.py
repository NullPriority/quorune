"""Lower closed resolution-time choices without announcing targets."""

from ..counter_placement_targets import controlled_creature_vigilance_counter_choice_effect
from ..rules.permanent_resolution_choices import (
    COUNTER_KEYWORDS_MECHANICS, TYPE_DESTRUCTION_MECHANICS,
    controlled_counter_keywords_choice, permanent_type_destruction_choice,
    token_then_vigilance_counter_dependencies,
)
from . import regex_working_set as re
from .fixed_effect_clause_sequences import FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC
from .token_templates import fixed_token_creation_effect_template


_TOKEN_THEN_VIGILANCE = re.compile(
    r"(?P<creation>Create .+? creature token), then put a vigilance counter on a creature you control\.",
    re.IGNORECASE,
)


def permanent_resolution_choice_template(text):
    if match := _TOKEN_THEN_VIGILANCE.fullmatch(text.strip()):
        token = fixed_token_creation_effect_template(match["creation"] + ".")
        if token is None or token.target_schema is not None:
            return None
        effects = (token.effect, controlled_creature_vigilance_counter_choice_effect())
        mechanics = (*token.mechanics, "cr-122-counters", "vigilance", FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC)
        if token_then_vigilance_counter_dependencies(effects, None, mechanics, None):
            return "fixed-token-then-vigilance-counter-choice-v1", effects, None, mechanics
        return None
    normalized = " ".join(text.casefold().split())
    if normalized == (
        "put a +1/+1 counter on a creature you control. "
        "it gains vigilance and trample until end of turn."
    ):
        return "fixed-controlled-counter-keywords-v1", (controlled_counter_keywords_choice(),), None, COUNTER_KEYWORDS_MECHANICS
    if normalized == "choose artifact or enchantment. destroy all permanents of the chosen type.":
        return "fixed-chosen-permanent-type-destruction-v1", (permanent_type_destruction_choice(),), None, TYPE_DESTRUCTION_MECHANICS
    return None
