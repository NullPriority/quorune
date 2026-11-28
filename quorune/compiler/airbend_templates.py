from __future__ import annotations

from . import regex_working_set as re
from .oracle_source_text import without_parenthetical_reminder
from .temporary_cast_restrictions import temporary_cast_restriction_template
from .any_number_target_sets import ANY_NUMBER_TARGET_MECHANIC
from ..rules.airbend import (
    TARGETED_TEMPLATE, TEMPLATE, airbend_excluding_optional_creature_effect, airbend_optional_creature_schema,
    airbend_targeted_nonland_effect, airbend_targeted_nonland_schema,
)


_AIRBEND_AND_RULE = re.compile(
    r"Choose up to one target creature, then airbend all other creatures\. "
    r"(?P<rule>Until your next turn, your opponents can't cast spells from anywhere other than their hands\.)", re.I,
)


def entry_targeted_airbend_template(text):
    if " ".join(without_parenthetical_reminder(text).split()).casefold() != (
        "airbend any number of other target nonland permanents you control."
    ):
        return None
    return (TARGETED_TEMPLATE, (airbend_targeted_nonland_effect(),), airbend_targeted_nonland_schema(),
            (ANY_NUMBER_TARGET_MECHANIC, "cr-115-targets"), None)


def airbend_and_cast_restriction_template(text):
    match = _AIRBEND_AND_RULE.fullmatch(" ".join(without_parenthetical_reminder(text).split()))
    if match is None:
        return None
    _, restriction, _, mechanics = temporary_cast_restriction_template(match["rule"])
    return (TEMPLATE, (airbend_excluding_optional_creature_effect(), *restriction), airbend_optional_creature_schema(),
            ("cr-115-targets", *mechanics))
