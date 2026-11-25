from __future__ import annotations

"""The remembered untap and attachment identity of a phased-out permanent."""

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousObjectIdentity


PHASING_CAPABILITY = "untap.phasing.keyword"
PHASING_EVENT = "untap.phasing"
PHASING_HANDLER_ID = "untap.phasing.printed.v1"
PHASING_TRIGGER_SOURCE_VISIBILITY_VERSION = 2


def phasing_lifecycle_version(value):
    if value is not None and (type(value) is not int or value not in {1, PHASING_TRIGGER_SOURCE_VISIBILITY_VERSION}):
        raise ValueError("Unsupported phasing lifecycle version")
    return value


@dataclass(frozen=True, slots=True)
class PhasingRecord:
    return_seat: str
    attached_object: ContinuousObjectIdentity | None = None
    attached_player: str | None = None
    indirect: bool = False
    departed_ready: bool = False

    def __post_init__(self):
        if type(self.return_seat) is not str or not self.return_seat:
            raise ValueError("Phasing requires the controller at phase-out")
        if self.attached_object is not None and not isinstance(self.attached_object, ContinuousObjectIdentity):
            raise ValueError("Phasing attachment requires a current object identity")
        if self.attached_player is not None and (
            type(self.attached_player) is not str or not self.attached_player or self.attached_object is not None
        ):
            raise ValueError("Phasing attachment player is malformed")
        if type(self.indirect) is not bool or type(self.departed_ready) is not bool:
            raise ValueError("Phasing flags must be boolean")
        if self.indirect and (self.attached_object is None or self.departed_ready):
            raise ValueError("Indirect phasing follows one attached permanent")

    def to_dict(self):
        return {
            "return_seat": self.return_seat,
            "attached_object": self.attached_object.to_dict() if self.attached_object is not None else None,
            "attached_player": self.attached_player,
            "indirect": self.indirect,
            "departed_ready": self.departed_ready,
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
            "return_seat", "attached_object", "attached_player", "indirect", "departed_ready",
        }:
            raise ValueError("Phasing record fields are closed")
        attached = value["attached_object"]
        return cls(
            return_seat=value["return_seat"],
            attached_object=ContinuousObjectIdentity.from_dict(attached) if attached is not None else None,
            attached_player=value["attached_player"], indirect=value["indirect"], departed_ready=value["departed_ready"],
        )


PHASE_OUT_OPERATION = "phase_out_permanent"
PHASE_OUT_CAPABILITY = "permanent.phasing.fixed_phase_out"
PHASE_OUT_MECHANIC = "fixed-phase-out"
PHASE_OUT_RULES = ("502.1", "506.4", "608.2b", "702.26b", "702.26d", "702.26g",
                   "702.26h", "702.26i", "702.26m", "702.26n")

PHASE_OUT_TARGET_SET_CAPABILITY = "permanent.phasing.announced_target_set"
PHASE_OUT_ANY_NUMBER_SET_CAPABILITY = "permanent.phasing.any_number_target_set"
PHASE_OUT_TARGET_SET_SUBJECT = "target_set"
CONTROLLED_LAND_PHASE_OUT_SUBJECT = "controlled_land_set"


def controlled_land_set_phase_out_effect():
    return {"op": PHASE_OUT_OPERATION, "schema_version": 4, "subject": CONTROLLED_LAND_PHASE_OUT_SUBJECT}


def controlled_land_set_phase_out_condition():
    return {"all": [
        {"field": "player", "op": "eq", "value": "$source.controller"},
        {"field": "step", "op": "eq", "value": "upkeep"},
    ]}


def announced_phase_out_effect() -> dict:
    from .query_effect_amount_model import CastXAmountSpec

    return {
        "op": PHASE_OUT_OPERATION,
        "schema_version": 2,
        "subject": PHASE_OUT_TARGET_SET_SUBJECT,
        "cards": "$targets",
        "maximum_targets": CastXAmountSpec().to_dict(),
        "announced_target_count": True,
    }


def has_announced_phase_out_targets(effects) -> bool:
    return any(effect.get("op") == PHASE_OUT_OPERATION
               and effect.get("subject") == PHASE_OUT_TARGET_SET_SUBJECT
               and type(effect.get("schema_version")) is int and effect["schema_version"] == 2
               and effect.get("announced_target_count") is True for effect in effects)


def any_number_phase_out_schema(*, other_creatures: bool) -> dict:
    from .target_cardinality import AnnouncedTargetCardinality

    return {
        "zones": ["battlefield"], "categories": ["permanent"], "controller_relation": "you",
        **({"types_any": ["creature"], "source_exclusion": True} if other_creatures else {"types_none": ["land"]}),
        "count_parameter": AnnouncedTargetCardinality(
            selection="up_to", parameter="any_number", schema_version=2,
        ).to_dict(),
    }


def any_number_phase_out_effect() -> dict:
    from .target_cardinality import selected_target_count_descriptor

    return {**announced_phase_out_effect(), "schema_version": 3,
            "maximum_targets": selected_target_count_descriptor()}


def _validate_phase_out_target_set(effect, *, runtime):
    version = effect["schema_version"]
    expected = announced_phase_out_effect() if version == 2 else any_number_phase_out_effect()
    fields = set(expected)
    if runtime and "_runtime_source" in effect:
        fields.add("_runtime_source")
    if set(effect) != fields or effect["announced_target_count"] is not True:
        raise ValueError("Phase out requires its sealed announced target set")
    if not runtime:
        if dict(effect) != expected:
            raise ValueError("Phase out targets must use their declared set limit")
        if version == 2:
            from .query_effect_amount_model import CastXAmountSpec

            if CastXAmountSpec.from_dict(effect["maximum_targets"]) != CastXAmountSpec():
                raise ValueError("The phase-out limit must use the spell's X")
        else:
            from .target_cardinality import validate_selected_target_count

            validate_selected_target_count(effect["maximum_targets"])
        return
    cards = effect["cards"]
    maximum = effect["maximum_targets"]
    if (not isinstance(cards, (list, tuple))
            or any(type(ref) is not str or not ref or ref != ref.strip() for ref in cards)
            or len(set(cards)) != len(cards)
            or type(maximum) is not int or maximum < len(cards)):
        raise ValueError("Phase out requires distinct current targets within announced X")



def phase_out_effect(*, targeted=False):
    return {"op": PHASE_OUT_OPERATION, "schema_version": 1,
            "subject": "target" if targeted else "source",
            **({"card": "$target.0"} if targeted else {})}


def validate_phase_out_effect(effect, *, runtime=False):
    if not isinstance(effect, Mapping) or effect.get("op") != PHASE_OUT_OPERATION:
        raise ValueError("Phase out requires its typed operation")
    subject = effect.get("subject")
    version = effect.get("schema_version")
    if subject == CONTROLLED_LAND_PHASE_OUT_SUBJECT or version == 4:
        values = dict(effect)
        if runtime:
            values.pop("_runtime_source", None)
        if type(version) is not int or values != controlled_land_set_phase_out_effect():
            raise ValueError("Land phasing requires its versioned current-controller land set")
        return subject
    if type(version) is int and version in (2, 3) and subject == PHASE_OUT_TARGET_SET_SUBJECT:
        _validate_phase_out_target_set(effect, runtime=runtime)
        return subject
    required = {"op", "schema_version", "subject"}
    if subject == "target":
        required.add("card")
        ref = effect.get("card")
        if type(ref) is not str or not ref or ref != ref.strip():
            raise ValueError("Phase out requires a single target reference")
    allowed = required | ({"_runtime_source"} if runtime else set())
    if (subject not in ("source", "target") or not required.issubset(effect)
            or set(effect) - allowed or type(effect["schema_version"]) is not int
            or effect["schema_version"] != 1):
        raise ValueError("Phase out requires a closed versioned source or target instruction")
    return subject
