from __future__ import annotations

"""The remembered untap and attachment identity of a phased-out permanent."""

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousObjectIdentity
from .replacement.immutable import FrozenMap
from .source_control_phase_in_model import (
    SOURCE_CONTROL_PHASE_OUT_SUBJECT, SOURCE_CONTROL_PHASE_OUT_VERSION, SourceControlPhaseInRestriction,
    validate_source_control_phase_out_effect,
)


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
    source_control_restriction: SourceControlPhaseInRestriction | None = None
    battlefield_departure_hold: ContinuousObjectIdentity | None = None
    linked_phase_in_tapped: bool = True

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
        if self.source_control_restriction is not None and (
            self.indirect or not isinstance(self.source_control_restriction, SourceControlPhaseInRestriction)
        ):
            raise ValueError("Only a directly phased-out object may carry a source-control restriction")
        if self.battlefield_departure_hold is not None and (
            not isinstance(self.battlefield_departure_hold, ContinuousObjectIdentity)
            or self.indirect or self.departed_ready or self.source_control_restriction is not None
        ):
            raise ValueError("Only one directly phased-out root may wait for its original battlefield source")
        if type(self.linked_phase_in_tapped) is not bool or (
            self.battlefield_departure_hold is None and not self.linked_phase_in_tapped
        ):
            raise ValueError("Only a linked phase-in may supersede its default tapped status recipe")

    def to_dict(self):
        return {
            "return_seat": self.return_seat,
            "attached_object": self.attached_object.to_dict() if self.attached_object is not None else None,
            "attached_player": self.attached_player,
            "indirect": self.indirect,
            "departed_ready": self.departed_ready,
            **({"source_control_restriction": self.source_control_restriction.to_dict()}
               if self.source_control_restriction is not None else {}),
            **({"battlefield_departure_hold": self.battlefield_departure_hold.to_dict()}
               if self.battlefield_departure_hold is not None else {}),
            **({"linked_phase_in_tapped": False} if not self.linked_phase_in_tapped else {}),
        }

    @classmethod
    def from_dict(cls, value):
        legacy = {"return_seat", "attached_object", "attached_player", "indirect", "departed_ready"}
        field = "source_control_restriction"
        hold = "battlefield_departure_hold"
        tap = "linked_phase_in_tapped"
        if (not isinstance(value, Mapping) or set(value) not in (legacy, legacy | {field}, legacy | {hold}, legacy | {hold, tap})
                or tap in value and value[tap] is not False):
            raise ValueError("Phasing record fields are closed")
        restriction = SourceControlPhaseInRestriction.from_dict(value[field]) if field in value else None
        attached = value["attached_object"]
        return cls(
            return_seat=value["return_seat"],
            attached_object=ContinuousObjectIdentity.from_dict(attached) if attached is not None else None,
            attached_player=value["attached_player"], indirect=value["indirect"], departed_ready=value["departed_ready"],
            source_control_restriction=restriction,
            battlefield_departure_hold=ContinuousObjectIdentity.from_dict(value[hold]) if hold in value else None,
            linked_phase_in_tapped=value.get(tap, True),
        )


PHASE_OUT_OPERATION = "phase_out_permanent"
PHASE_OUT_CAPABILITY = "permanent.phasing.fixed_phase_out"
PHASE_OUT_MECHANIC = "fixed-phase-out"
PHASE_OUT_RULES = ("502.1", "506.4", "608.2b", "702.26b", "702.26d", "702.26g",
                   "702.26h", "702.26i", "702.26m", "702.26n")

PHASE_OUT_TARGET_SET_CAPABILITY = "permanent.phasing.announced_target_set"
PHASE_OUT_ANY_NUMBER_SET_CAPABILITY = "permanent.phasing.any_number_target_set"
PHASE_OUT_TARGET_SET_SUBJECT = "target_set"
SPELL_TARGET_PHASE_OUT_SUBJECT = "spell_target_occurrence"
SPELL_TARGET_PHASE_OUT_VERSION = 9
BATTLEFIELD_PHASE_OUT_VERSION = 10
BATTLEFIELD_PHASE_OUT_SUBJECT = "target_until_battlefield_departure"
CREATURE_SET_PHASE_HOLD_VERSION = 11
CREATURE_SET_PHASE_HOLD_SUBJECT = "untap_and_count_held_creatures"


def creature_set_phase_hold_effect():
    return {"op": PHASE_OUT_OPERATION, "schema_version": CREATURE_SET_PHASE_HOLD_VERSION,
            "subject": CREATURE_SET_PHASE_HOLD_SUBJECT}


def uses_counted_creature_phase_hold(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == PHASE_OUT_OPERATION
            and effect.get("subject") == CREATURE_SET_PHASE_HOLD_SUBJECT)


def spell_target_phase_out_effect():
    return {"op": PHASE_OUT_OPERATION, "schema_version": SPELL_TARGET_PHASE_OUT_VERSION,
            "subject": SPELL_TARGET_PHASE_OUT_SUBJECT}

OPTIONAL_ENTRY_PHASE_OUT_VERSION = 8
OPTIONAL_ENTRY_PHASE_OUT_CAPABILITY = "permanent.phasing.optional_single_creature_entry"
PROLIFERATE_PHASE_OUT_SUBJECT = "proliferate_counter_recipients"
PROLIFERATE_PHASE_OUT_VERSION = 7
CONTROLLED_LAND_PHASE_OUT_SUBJECT = "controlled_land_set"
CONTROLLED_PERMANENT_PHASE_OUT_SUBJECT = "controlled_permanent_set"
CONTROLLED_PERMANENT_PHASE_OUT_CAPABILITY = "permanent.phasing.controlled_permanent_set"
TARGET_PLAYER_NONLAND_PHASE_OUT_SUBJECT = "target_player_nonland_set"
TARGET_PLAYER_NONLAND_PHASE_OUT_VERSION = 12


def target_player_nonland_phase_out_effect():
    return {"op": PHASE_OUT_OPERATION, "schema_version": TARGET_PLAYER_NONLAND_PHASE_OUT_VERSION,
            "subject": TARGET_PLAYER_NONLAND_PHASE_OUT_SUBJECT, "player": "$target.0", "source": "$stack"}


def validate_target_player_nonland_phase_out(effect, *, runtime):
    expected = target_player_nonland_phase_out_effect()
    fields = dict(effect)
    if runtime:
        for field in ("player", "source"):
            if type(fields.get(field)) is not str or not fields[field]:
                raise ValueError("Nonland phasing requires a bound player and stack reference")
            expected[field] = fields[field]
        fields.pop("_runtime_source", None)
        fields.pop("reason", None)
    if type(fields.get("schema_version")) is not int or fields != expected:
        raise ValueError("Nonland phasing requires the exact selected player's nonland set")
    return expected["subject"]


def controlled_permanent_set_phase_out_effect():
    return {"op": PHASE_OUT_OPERATION, "schema_version": 5, "subject": CONTROLLED_PERMANENT_PHASE_OUT_SUBJECT}


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


def optional_entry_phase_out_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"],
            "types_any": ["creature"], "up_to": 1}


def optional_entry_phase_out_effect():
    return {"op": PHASE_OUT_OPERATION, "schema_version": OPTIONAL_ENTRY_PHASE_OUT_VERSION,
            "subject": PHASE_OUT_TARGET_SET_SUBJECT, "cards": "$targets",
            "maximum_targets": 1}


def _validate_phase_out_target_set(effect, *, runtime):
    version = effect["schema_version"]
    if version == OPTIONAL_ENTRY_PHASE_OUT_VERSION:
        expected = optional_entry_phase_out_effect()
    else:
        expected = announced_phase_out_effect() if version == 2 else any_number_phase_out_effect()
    if version == OPTIONAL_ENTRY_PHASE_OUT_VERSION and (
        type(effect.get("maximum_targets")) is not int or effect["maximum_targets"] != 1
    ):
        raise ValueError("Optional entry phase-out admits at most one creature")
    fields = set(expected)
    if runtime and "_runtime_source" in effect:
        fields.add("_runtime_source")
    if set(effect) != fields or (version in (2, 3) and effect.get("announced_target_count") is not True):
        raise ValueError("Phase out requires its sealed announced target set")
    if not runtime:
        if dict(effect) != expected:
            raise ValueError("Phase out targets must use their declared set limit")
        if version == 2:
            from .query_effect_amount_model import CastXAmountSpec

            if CastXAmountSpec.from_dict(effect["maximum_targets"]) != CastXAmountSpec():
                raise ValueError("The phase-out limit must use the spell's X")
        elif version == 3:
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


def validate_phase_out_recipient_identities(raw):
    if not isinstance(raw, (tuple, list)):
        raise ValueError("Counter recipients must be a canonical sequence")
    fields = {"object_id", "logical_object_id", "ref"}
    if any(not isinstance(row, Mapping) or set(row) != fields
           or any(type(value) is not str or not value or value != value.strip() for value in row.values())
           for row in raw):
        raise ValueError("Counter recipients require exact permanent incarnations")
    if any(len({row[field] for row in raw}) != len(raw) for field in fields):
        raise ValueError("Counter recipient identities must be distinct")
    return tuple(FrozenMap(row) for row in raw)


def selected_counter_phase_out_effect(recipients):
    return {
        "op": PHASE_OUT_OPERATION,
        "schema_version": PROLIFERATE_PHASE_OUT_VERSION,
        "subject": PROLIFERATE_PHASE_OUT_SUBJECT,
        "recipients": validate_phase_out_recipient_identities(recipients),
    }


def validate_selected_counter_phase_out(effect, *, runtime):
    fields = {"op", "schema_version", "subject", "recipients"}
    if (not runtime or set(effect) not in (fields, fields | {"_runtime_source"})
            or effect["op"] != PHASE_OUT_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != PROLIFERATE_PHASE_OUT_VERSION
            or effect["subject"] != PROLIFERATE_PHASE_OUT_SUBJECT):
        raise ValueError("A counter-recipient phase-out needs its completed selection")
    return validate_phase_out_recipient_identities(effect["recipients"])


def battlefield_phase_out_effect():
    return {"op": "phase_out_permanent", "schema_version": BATTLEFIELD_PHASE_OUT_VERSION,
            "subject": BATTLEFIELD_PHASE_OUT_SUBJECT, "card": "$target.0"}


def validate_battlefield_phase_out(effect, *, runtime=False):
    values = dict(effect)
    expected = battlefield_phase_out_effect()
    if runtime:
        values.pop("_runtime_source", None)
        target = values.get("card")
        if type(target) is not str or not target or target.startswith("$"):
            raise ValueError("A phasing hold requires its original revalidated creature")
        expected["card"] = target
    if type(values.get("schema_version")) is not int or values != expected:
        raise ValueError("A battlefield phase hold requires its exact closed instruction")


def validate_phase_out_effect(effect, *, runtime=False):
    if not isinstance(effect, Mapping) or effect.get("op") != PHASE_OUT_OPERATION:
        raise ValueError("Phase out requires its typed operation")
    subject = effect.get("subject")
    version = effect.get("schema_version")
    if subject == CREATURE_SET_PHASE_HOLD_SUBJECT or version == CREATURE_SET_PHASE_HOLD_VERSION:
        values = {key: value for key, value in effect.items() if not runtime or key != "_runtime_source"}
        if type(version) is not int or values != creature_set_phase_hold_effect():
            raise ValueError("Counted creature phasing requires its closed untap-and-hold subject")
        return subject
    if subject == BATTLEFIELD_PHASE_OUT_SUBJECT or version == BATTLEFIELD_PHASE_OUT_VERSION:
        validate_battlefield_phase_out(effect, runtime=runtime)
        return subject
    if subject == SPELL_TARGET_PHASE_OUT_SUBJECT or version == SPELL_TARGET_PHASE_OUT_VERSION:
        values = {key: value for key, value in effect.items() if not runtime or key != "_runtime_source"}
        if type(version) is not int or values != spell_target_phase_out_effect():
            raise ValueError("Occurrence phase-out requires its closed spell-target subject")
        return subject
    if subject == PROLIFERATE_PHASE_OUT_SUBJECT or version == PROLIFERATE_PHASE_OUT_VERSION:
        validate_selected_counter_phase_out(effect, runtime=runtime)
        return subject
    if subject == SOURCE_CONTROL_PHASE_OUT_SUBJECT or version == SOURCE_CONTROL_PHASE_OUT_VERSION:
        return validate_source_control_phase_out_effect(effect, runtime=runtime)
    if subject == CONTROLLED_LAND_PHASE_OUT_SUBJECT or version == 4:
        values = dict(effect)
        if runtime:
            values.pop("_runtime_source", None)
        if type(version) is not int or values != controlled_land_set_phase_out_effect():
            raise ValueError("Land phasing requires its versioned current-controller land set")
        return subject
    if subject == TARGET_PLAYER_NONLAND_PHASE_OUT_SUBJECT or version == TARGET_PLAYER_NONLAND_PHASE_OUT_VERSION:
        return validate_target_player_nonland_phase_out(effect, runtime=runtime)
    if subject == CONTROLLED_PERMANENT_PHASE_OUT_SUBJECT or version == 5:
        values = dict(effect)
        if runtime:
            values.pop("_runtime_source", None)
        if type(version) is not int or values != controlled_permanent_set_phase_out_effect():
            raise ValueError("Permanent phasing requires its versioned current-controller set")
        return subject
    if type(version) is int and version in (2, 3, OPTIONAL_ENTRY_PHASE_OUT_VERSION) and subject == PHASE_OUT_TARGET_SET_SUBJECT:
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
