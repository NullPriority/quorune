"""A phase-in restriction tied to one uninterrupted control interval."""

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousObjectIdentity
from .replacement.immutable import FrozenMap
from .source_continuity import SourceContinuitySnapshot
from .source_tap_effect import RUNTIME_SOURCE_CONTINUITY_FIELD


SOURCE_CONTROL_PHASE_OUT_SUBJECT = "target_under_source_control_duration"
SOURCE_CONTROL_PHASE_OUT_VERSION = 6
SOURCE_CONTROL_PHASE_OUT_CAPABILITY = "permanent.phasing.source_control_phase_in_restriction"
SOURCE_CONTROL_PHASE_OUT_TARGET = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
    "types_none": ["land"], "source_exclusion": True,
})


def source_control_phase_out_effect():
    return {
        "op": "phase_out_permanent", "schema_version": SOURCE_CONTROL_PHASE_OUT_VERSION,
        "subject": SOURCE_CONTROL_PHASE_OUT_SUBJECT, "card": "$target.0",
    }


def uses_source_control_phase_out(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == "phase_out_permanent"
            and effect.get("subject") == SOURCE_CONTROL_PHASE_OUT_SUBJECT)


def validate_source_control_phase_out_effect(effect, *, runtime):
    if not isinstance(effect, Mapping):
        raise ValueError("Source-controlled phasing requires a typed instruction")
    actual = dict(effect)
    expected = source_control_phase_out_effect()
    if runtime:
        actual.pop("_runtime_source", None)
        actual.pop(RUNTIME_SOURCE_CONTINUITY_FIELD, None)
        target = actual.get("card")
        if type(target) is not str or not target or target.startswith("$"):
            raise ValueError("Source-controlled phasing requires the current original target")
        expected["card"] = target
    if type(actual.get("schema_version")) is not int or actual != expected:
        raise ValueError("Source-controlled phasing requires its closed original target and duration")
    return SOURCE_CONTROL_PHASE_OUT_SUBJECT


@dataclass(frozen=True, slots=True)
class SourceControlPhaseInRestriction:
    source: ContinuousObjectIdentity
    controller: str
    continuity: SourceContinuitySnapshot

    def __post_init__(self):
        if (not isinstance(self.source, ContinuousObjectIdentity)
                or type(self.controller) is not str or not self.controller or self.controller.startswith("$")
                or not isinstance(self.continuity, SourceContinuitySnapshot)):
            raise ValueError("A phase-in restriction requires its original public source interval")

    def to_dict(self):
        return {
            "schema_version": 1, "source": self.source.to_dict(),
            "controller": self.controller, "continuity": self.continuity.to_dict(),
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "source", "controller", "continuity"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("A source-control phase-in restriction has a closed versioned shape")
        return cls(
            ContinuousObjectIdentity.from_dict(value["source"]), value["controller"],
            SourceContinuitySnapshot.from_dict(value["continuity"]),
        )
