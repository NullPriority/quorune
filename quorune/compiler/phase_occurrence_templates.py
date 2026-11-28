from __future__ import annotations

"""Closed source-or-controlled-subtype phasing trigger grammar."""

from . import regex_working_set as re
from .fixed_public_event_trigger_bindings import _spec
from ..creature_subtypes import canonical_creature_subtype
from ..rules.phase_occurrence_model import (
    PHASE_IN_CAPABILITY, PHASE_IN_EVENT, PHASE_IN_MECHANIC, PHASE_IN_TOKEN_VARIANT, SPELL_TARGET_PHASE_OUT_MECHANIC,
    SPELL_TARGET_PHASE_OUT_VARIANT, observed_subtype, source_or_controlled_subtype_condition,
)
from ..phasing_model import PHASE_OUT_MECHANIC, spell_target_phase_out_effect
from ..rules.source_references import SourceReferenceSpec
from ..rules.target_announcements import TARGET_ANNOUNCEMENT_CAPABILITY, TARGET_ANNOUNCEMENT_EVENT


_TRIGGER = re.compile(
    r"Whenever (?P<source>.+?) or another (?P<subtype>.+?) you control "
    r"(?P<event>becomes the target of a spell|phases in), (?P<body>.+)", re.I,
)
_TOKEN = re.compile(r"create a tapped 1/1 white (?P<subtype>.+?) creature token with flying\.", re.I)


def phase_occurrence_binding_spec(material_line, *, card_name=None):
    match = _TRIGGER.fullmatch(material_line)
    if match is None or not card_name or not SourceReferenceSpec(card_name).matches(match['source']):
        return None
    subtype = canonical_creature_subtype(match['subtype'])
    if subtype is None:
        return None
    body = match['body']
    if match['event'].casefold() == "becomes the target of a spell":
        if body.casefold() != "it phases out.":
            return None
        return _spec(TARGET_ANNOUNCEMENT_EVENT, SPELL_TARGET_PHASE_OUT_VARIANT, body,
            "fixed-spell-target-occurrence-phase-out-v1", "trigger-event-normalized-target-announcement",
            condition=source_or_controlled_subtype_condition(subtype, spell_target=True),
            capabilities=(TARGET_ANNOUNCEMENT_CAPABILITY,))
    token = _TOKEN.fullmatch(body)
    if token is None or canonical_creature_subtype(token['subtype']) != subtype:
        return None
    return _spec(PHASE_IN_EVENT, PHASE_IN_TOKEN_VARIANT, body, "fixed-phase-in-token-v1", PHASE_IN_MECHANIC,
        condition=source_or_controlled_subtype_condition(subtype, spell_target=False), capabilities=(PHASE_IN_CAPABILITY,))


def spell_target_occurrence_phase_out_result(binding, body):
    if (binding.variant != SPELL_TARGET_PHASE_OUT_VARIANT or body != binding.body
            or binding.event.value != TARGET_ANNOUNCEMENT_EVENT or body.casefold() != "it phases out."
            or observed_subtype(binding.event_condition, spell_target=True) is None):
        return None
    return ("spell-target-occurrence-phase-out-v1", (spell_target_phase_out_effect(),), None,
            (PHASE_OUT_MECHANIC, SPELL_TARGET_PHASE_OUT_MECHANIC))
