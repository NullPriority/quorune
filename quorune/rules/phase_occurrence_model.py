from __future__ import annotations

"""Public phase-in observations and closed source-or-controlled-subtype conditions."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import FrozenMap


PHASE_IN_EVENT = "permanent.phases_in"
PHASE_IN_CAPABILITY = "trigger.event.normalized_phase_in"
PHASE_IN_MECHANIC = "trigger-event-normalized-phase-in"
PHASE_IN_TOKEN_VARIANT = "source_or_controlled_subtype_phases_in"
SPELL_TARGET_PHASE_OUT_CAPABILITY = "trigger.effect.spell_target_occurrence_phase_out"
SPELL_TARGET_PHASE_OUT_MECHANIC = "fixed-spell-target-occurrence-phase-out"
SPELL_TARGET_PHASE_OUT_VARIANT = "source_or_controlled_subtype_becomes_spell_target"
SPELL_STACK_KINDS = ("spell", "spell_copy")


@dataclass(frozen=True, slots=True)
class PhaseInOccurrence:
    card_ref: str
    controller: str
    logical_object_id: str
    subtypes: tuple[str, ...]

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.card_ref, self.controller, self.logical_object_id))
                or type(self.subtypes) is not tuple or self.subtypes != tuple(sorted(set(self.subtypes)))
                or any(type(value) is not str or not value for value in self.subtypes)):
            raise ValueError("A phase-in occurrence requires its current public incarnation and subtypes")

    def to_context(self):
        return {"schema_version": 1, "card": self.card_ref, "controller": self.controller,
                "card_object_identity": self.logical_object_id, "subtypes": list(self.subtypes)}


def source_or_controlled_subtype_condition(subtype, *, spell_target):
    if canonical_creature_subtype(subtype) != subtype or subtype is None:
        raise ValueError("The observation requires one canonical creature subtype")
    subject = {"any": [
        {"field": "card", "op": "eq", "value": "$source.ref"},
        {"all": [
            {"field": "controller", "op": "eq", "value": "$source.controller"},
            {"field": "subtypes", "op": "contains_any", "value": [subtype]},
        ]},
    ]}
    if spell_target:
        return {"all": [{"field": "stack_kind", "op": "in", "value": list(SPELL_STACK_KINDS)}, subject]}
    return subject


def observed_subtype(condition, *, spell_target):
    if not isinstance(condition, Mapping):
        return None
    try:
        subject = condition["all"][1] if spell_target else condition
        subtype, = subject["any"][1]["all"][1]["value"]
        expected = source_or_controlled_subtype_condition(subtype, spell_target=spell_target)
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    return subtype if FrozenMap(condition) == FrozenMap(expected) else None
