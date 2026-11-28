from __future__ import annotations

"""One printed fixed color grants protection to the source incarnation."""

from . import regex_working_set as re

from ..rules.source_references import SourceReferenceSpec
from .source_protection_choices import source_color_protection_effect


MECHANIC = 'temporary-source-fixed-protection'
CAPABILITY = 'continuous.resolution.source_fixed_protection'


def source_fixed_protection_template(text, *, source_is_permanent, source_card_types, source_name):
    if source_is_permanent is not True or 'creature' not in {str(x).casefold() for x in source_card_types}:
        return None
    subject = rf'(?:this creature|{SourceReferenceSpec(source_name).regex_pattern})' if source_name else r'this creature'
    match = re.fullmatch(rf'{subject} gains protection from (?P<color>white|blue|black|red|green) until end of turn\.?',
        ' '.join(text.strip().split()), re.I)
    if match is None:
        return None
    effect = source_color_protection_effect(match['color'].title())
    return 'temporary-source-fixed-protection-v1', (effect,), None, (MECHANIC, 'protection', 'cr-611-continuous-effects')


def each_color_protection_template(text):
    from ..rules.temporary_ability_grant import (
        TEMPORARY_QUOTED_GRANT_MECHANIC, each_color_protection_instruction,
    )

    match = re.fullmatch(
        r"(?P<subject>target|each) creature you control gains protection from each color until end of turn\.",
        " ".join(text.split()), re.I,
    )
    if match is None:
        return None
    targeted = match["subject"].casefold() == "target"
    instruction, schema = each_color_protection_instruction(targeted=targeted)
    mechanics = (TEMPORARY_QUOTED_GRANT_MECHANIC, "protection", "cr-611-continuous-effects")
    return ("temporary-each-color-protection-v1", (instruction,), schema,
            (*mechanics, *(("cr-115-targets",) if targeted else ())))
