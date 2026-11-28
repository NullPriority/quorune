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
    from ..rules.reflexive_defender_exile import attack_condition, child_program

    attack = binding.event.value == "creature.attacks" and binding.event_condition == attack_condition()
    beginning = binding.event.value == "step.begin" and binding.event_condition == source_counter_reflexive_condition()
    if binding.active_zone != "battlefield" or binding.body != text or not (attack or beginning):
        return None
    match = re.fullmatch(r"Put a \+1/\+1 counter on (?P<source>[^.]+)\. When you do, (?P<body>.+)",
                         " ".join(text.split()), re.IGNORECASE)
    if match is None:
        return None
    subject = match["source"]
    same_source = subject.casefold() == "this creature" or SourceReferenceSpec(card_name).matches(subject)
    if not (same_source or attack and subject.casefold() == "it"):
        return None
    if attack:
        exile = re.fullmatch(
            r"Exile target creature defending player controls with power less than (?P<power>.+?)['’]s power "
            r"until (?P<duration>.+?) leaves the battlefield\.",
            match["body"], re.I,
        )
        source = SourceReferenceSpec(card_name)
        if exile is None or not all(exile[key].casefold() == "this creature" or source.matches(exile[key])
                                    for key in ("power", "duration")):
            return None
        child = child_program()
    else:
        schema = source_optional_creature_unblockable(match["body"], source_name=card_name)
        if schema is None or schema.get("controller_relation") != "you":
            return None
        child = ReflexiveTriggerProgram(
            (source_optional_unblockable_effect(),), schema, source_optional_unblockable_mechanics(),
            schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION,
        )
    effect = {**SOURCE_COUNTER_EFFECT, SOURCE_COUNTER_REFLEXIVE_FIELD: child.to_dict()}
    return ("source-counter-and-reflexive-declaration-v1", (effect,), None, SOURCE_COUNTER_REFLEXIVE_MECHANICS)
