"""Compile an original combat counter and its separately targeted reflexive result."""

from . import regex_working_set as re
from .source_optional_creature_unblockable import source_optional_creature_unblockable
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, SOURCE_COUNTER_REFLEXIVE_FIELD, ReflexiveTriggerProgram,
)
from ..rules.source_references import SourceReferenceSpec
from ..rules.source_counter_reflexive import (
    SOURCE_COUNTER_EFFECT, SOURCE_COUNTER_REFLEXIVE_MECHANICS, source_counter_reflexive_condition,
)
from ..rules.source_optional_unblockable import source_optional_unblockable_effect, source_optional_unblockable_mechanics


def source_counter_reflexive_result(binding, text, *, card_name):
    if (binding.event.value != "step.begin" or binding.active_zone != "battlefield"
            or binding.body != text or binding.event_condition != source_counter_reflexive_condition()):
        return None
    match = re.fullmatch(r"Put a \+1/\+1 counter on (?P<source>[^.]+)\. When you do, (?P<body>.+)",
                         " ".join(text.split()), re.IGNORECASE)
    if match is None:
        return None
    if match["source"].casefold() != "this creature" and not SourceReferenceSpec(card_name).matches(match["source"]):
        return None
    schema = source_optional_creature_unblockable(match["body"], source_name=card_name)
    if schema is None or schema.get("controller_relation") != "you":
        return None
    child = ReflexiveTriggerProgram(
        (source_optional_unblockable_effect(),), schema, source_optional_unblockable_mechanics(),
        schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION,
    )
    effect = {**SOURCE_COUNTER_EFFECT, SOURCE_COUNTER_REFLEXIVE_FIELD: child.to_dict()}
    return ("source-counter-and-reflexive-declaration-v1", (effect,), None, SOURCE_COUNTER_REFLEXIVE_MECHANICS)
