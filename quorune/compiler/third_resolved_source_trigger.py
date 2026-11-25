"""Compile an original death-trigger counter and its third-resolution reflexive fight."""

from . import regex_working_set as re
from .creature_power_damage_templates import fixed_creature_power_damage_effect_template
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, REFLEXIVE_PAYMENT_MECHANIC, THIRD_SOURCE_TRIGGER_FIELD, ReflexiveTriggerProgram,
)
from ..rules.source_references import SourceReferenceSpec
from ..rules.third_resolved_source_trigger import (
    SOURCE_COUNTER, THIRD_SOURCE_TRIGGER_CONDITION, third_source_trigger_child,
)


_BODY = re.compile(
    r"Put a \+1/\+1 counter on (?P<source>[^.]+)\. When this ability resolves for the "
    r"third time this turn, (?P<fight>[^.]+\.)",
    re.IGNORECASE,
)


def third_resolved_source_trigger_result(binding, body, *, card_name):
    if (binding.event.value != "creature.dies" or binding.active_zone != "battlefield"
            or binding.event_condition != THIRD_SOURCE_TRIGGER_CONDITION or binding.body != body):
        return None
    match = _BODY.fullmatch(" ".join(body.split()))
    if match is None:
        return None
    source = match["source"].casefold()
    if source not in {"this creature", "it"} and not SourceReferenceSpec(card_name).matches(match["source"]):
        return None
    fight = fixed_creature_power_damage_effect_template(
        match["fight"], card_name=card_name, allow_source_pronoun=True,
    )
    if (fight is None or fight.template_id != "fixed-source-creature-fight-v1" or len(fight.effects) != 1):
        return None
    consequence = {**fight.effects[0], "source": "$source.zone_object", "source_lki": None}
    child = ReflexiveTriggerProgram(
        (consequence,), fight.target_schema, fight.mechanics, schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION,
    )
    try:
        third_source_trigger_child(child.to_dict())
    except (TypeError, ValueError, KeyError):
        return None
    effect = {**SOURCE_COUNTER, THIRD_SOURCE_TRIGGER_FIELD: child.to_dict()}
    mechanics = ("cr-122-counters", REFLEXIVE_PAYMENT_MECHANIC, *child.mechanic_ids)
    return "third-resolved-trigger-fight-v1", (effect,), None, mechanics
