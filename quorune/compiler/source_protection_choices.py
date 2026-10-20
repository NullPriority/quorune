from __future__ import annotations

"""One resolution-time color choice for the source creature incarnation."""

import re

from ..ability_fragments import ability_fragment_to_dict, parse_protection_line
from ..rules.source_references import SourceReferenceSpec

MECHANIC = "temporary-source-protection-choice"
CAPABILITY = "continuous.resolution.source_protection_choice"
COLORS = (("W", "White"), ("U", "Blue"), ("B", "Black"), ("R", "Red"), ("G", "Green"))


def source_color_protection_effect(word):
    fragment, = parse_protection_line('Protection from ' + word)
    return {"op": "apply_source_characteristics_until_end_of_turn", "schema_version": 7,
        "card": "$source.zone_object", "keywords": [],
        "ability_fragments": [ability_fragment_to_dict(fragment)],
        "exclude_source": False, "printed_only": False}


def source_protection_choice_template(text, *, source_is_permanent, source_card_types, source_name):
    if source_is_permanent is not True or "creature" not in {str(value).casefold() for value in source_card_types}:
        return None
    subject = rf"(?:this creature|{SourceReferenceSpec(source_name).regex_pattern})" if source_name else r"this creature"
    if re.fullmatch(rf"{subject} gains protection from the color of your choice until end of turn\.?",
                    " ".join(text.strip().split()), re.I) is None:
        return None
    branches = {}
    for symbol, word in COLORS:
        branches[symbol] = [source_color_protection_effect(word)]
    effect = {"op": "choose_option", "player": "$controller", "prompt": "Choose a protection color.",
        "options": [{"id": symbol, "label": word} for symbol, word in COLORS], "then_by_choice": branches}
    return "temporary-source-protection-choice-v1", (effect,), None, (MECHANIC, "protection", "cr-611-continuous-effects")
