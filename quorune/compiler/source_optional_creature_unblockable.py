"""Source plus up to one ordinary creature affected by the same declaration rule."""

from . import regex_working_set as re
from ..rules.source_references import SourceReferenceSpec
from ..rules.linked_clue_copy import linked_clue_sacrifice_condition
from ..rules.source_optional_unblockable import (
    source_optional_unblockable_effect, source_optional_unblockable_mechanics,
    source_optional_unblockable_target_schema,
)


def source_optional_creature_unblockable(text, *, source_name):
    """Recognize the source role and the one optional, separately targeted recipient."""
    match = re.fullmatch(
        r"(?P<source>.+?) and up to one other target creature(?P<controlled> you control)? "
        r"can['’]t be blocked this turn\.",
        " ".join(text.split()), re.IGNORECASE,
    )
    if match is None:
        return None
    subject = match["source"]
    if subject.casefold() != "this creature" and not SourceReferenceSpec(source_name).matches(subject):
        return None
    return source_optional_unblockable_target_schema(controlled=bool(match["controlled"]))


def clue_sacrifice_source_optional_unblockable(binding, text, *, card_name):
    if (binding.event.value != "permanent.sacrificed" or binding.active_zone != "battlefield"
            or binding.body != text or binding.event_condition != linked_clue_sacrifice_condition()):
        return None
    schema = source_optional_creature_unblockable(text, source_name=card_name)
    if schema is None:
        return None
    return (
        "source-and-optional-creature-unblockable-v1", (source_optional_unblockable_effect(),),
        schema, source_optional_unblockable_mechanics(),
    )
