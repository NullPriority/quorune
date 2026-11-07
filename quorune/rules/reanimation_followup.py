from __future__ import annotations

"""Closed battlefield entry riders and the resulting delayed departure."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from ..effect_contracts import REANIMATE_OPERATION
from ..delayed_object_model import RESOLVE_OBJECT_ACTION


REANIMATION_FOLLOWUP_CAPABILITY = "zone.reanimate.fixed_haste_and_departure"
REANIMATION_FOLLOWUP_MECHANIC = "fixed-target-reanimation-haste-and-departure"
REANIMATION_FOLLOWUP_VERSION = 4
LINKED_DEPARTURE_VERSION = 3

DEPARTURE_DESTINATIONS = {"exile": "exile", "sacrifice": "graveyard", "bounce": "hand"}
_REANIMATION_FIELDS = {"op", "schema_version", "card", "controller", "tapped", "followup"}
_RUNTIME_FIELDS = {"_runtime_source", "_replacement_selections", "_aura_target_ref", "reason"}


@dataclass(frozen=True, slots=True)
class BattlefieldEntryFollowup:
    haste_duration: str
    departure: str
    end_step: str
    power_boost: int = 0

    def __post_init__(self):
        if type(self.haste_duration) is not str or self.haste_duration not in ("zone_object", "until_end_of_turn"):
            raise ValueError("Return follow-up requires a represented haste duration")
        if type(self.departure) is not str or self.departure not in DEPARTURE_DESTINATIONS:
            raise ValueError("Return follow-up has an unsupported departure")
        if type(self.end_step) is not str or self.end_step not in ("next", "controllers_next"):
            raise ValueError("Return follow-up requires a supported end step")
        if type(self.power_boost) is not int or self.power_boost not in (0, 2):
            raise ValueError("Return follow-up power modifier is outside the closed family")
        if self.power_boost and (self.haste_duration, self.departure, self.end_step) != (
            "until_end_of_turn", "sacrifice", "next",
        ):
            raise ValueError("The power boost requires the complete temporary sacrifice instruction")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
            "haste_duration", "departure", "end_step", "power_boost",
        }:
            raise ValueError("Return follow-up fields are closed")
        return cls(**value)


def validate_reanimation_followup(effect, *, materialized=False):
    fields = _REANIMATION_FIELDS | ({"target_snapshot"} if materialized else set())
    allowed = fields | (_RUNTIME_FIELDS if materialized else set())
    if (not isinstance(effect, Mapping) or not fields <= set(effect) <= allowed
        or effect["op"] != REANIMATE_OPERATION or type(effect["schema_version"]) is not int
        or effect["schema_version"] != REANIMATION_FOLLOWUP_VERSION or type(effect["tapped"]) is not bool):
        raise ValueError("Linked reanimation requires its complete version-four instruction")
    if materialized:
        _validate_subject(effect["target_snapshot"])
        if (effect["target_snapshot"]["ref"] != effect["card"]
            or type(effect["controller"]) is not str or not effect["controller"]):
            raise ValueError("Linked reanimation requires a bound target and controller")
    elif effect["card"] != "$target.0" or effect["controller"] not in ("$controller", "$target.owner.0"):
        raise ValueError("Linked reanimation requires its declared target and controller")
    return BattlefieldEntryFollowup.from_dict(effect["followup"])


def _validate_subject(value):
    fields = {"ref", "object_id", "zone_change_counter"}
    if (not isinstance(value, Mapping) or set(value) != fields
        or type(value["zone_change_counter"]) is not int or value["zone_change_counter"] < 0
        or any(type(value[key]) is not str or not value[key] for key in fields - {"zone_change_counter"})):
        raise ValueError("A linked zone move requires a closed object incarnation")


def current_linked_subject(host, subject, *, zone):
    card = host.state.cards.get(subject["object_id"])
    if (card is None or card.zone != zone or card.phased_out or card.ref != subject["ref"]
        or card.zone_change_counter != subject["zone_change_counter"]):
        return None
    return card


def validate_linked_departure(effect):
    fields = {"op", "schema_version", "object", "action"}
    if (not isinstance(effect, Mapping) or not fields <= set(effect) <= fields | {"_replacement_selections"}
        or effect["op"] != RESOLVE_OBJECT_ACTION or type(effect["schema_version"]) is not int
        or effect["schema_version"] != LINKED_DEPARTURE_VERSION or type(effect["action"]) is not str
        or effect["action"] not in DEPARTURE_DESTINATIONS):
        raise ValueError("Delayed departure requires a closed version-three instruction")
    subject = effect["object"]
    _validate_subject(subject)
    if not isinstance(effect.get("_replacement_selections", ()), (tuple, list)):
        raise ValueError("Delayed departure replacement selections must be a sequence")
    return subject


def linked_departure_intent(effect, *, actor, reason):
    from ..semantic_runtime.zone_move_intent import ZoneMoveIntent
    from ..zone_trigger_events import ZoneTransitionKind

    subject = validate_linked_departure(effect)
    action = effect["action"]
    return ZoneMoveIntent(actor=actor, object_ref=subject["object_id"], expected_zones=("battlefield",),
        destination=DEPARTURE_DESTINATIONS[action], reason=reason, controlled_only=action == "sacrifice",
        optional_if_missing=True, expected_zone_change_counter=subject["zone_change_counter"],
        transition_kind=ZoneTransitionKind.SACRIFICE if action == "sacrifice" else ZoneTransitionKind.ORDINARY,
        replacement_selections=tuple(effect.get("_replacement_selections", ())))


def commit_battlefield_entry_followup(host, card, *, actor, source, followup):
    from ..continuous_effect_state import (
        ContinuousEffectDuration, ContinuousOperation, Layer, ResolutionContinuousComponent,
        create_resolution_continuous_effect_components,
    )
    from ..trigger_processing import schedule_delayed_trigger

    components = [ResolutionContinuousComponent(Layer.ABILITY, "6", (ContinuousOperation("add_ability", "Haste"),))]
    if followup.power_boost:
        components.append(ResolutionContinuousComponent(Layer.POWER_TOUGHNESS, "7c",
            (ContinuousOperation("modify_power_toughness", (followup.power_boost, 0)),)))
    create_resolution_continuous_effect_components(host, source=source, targets=(card,),
        components=tuple(components), duration=ContinuousEffectDuration(followup.haste_duration))
    condition = {"phase": "ending", "step": "end_step"}
    if followup.end_step == "controllers_next":
        condition["player"] = "controller"
    label = {"exile": "Delayed exile", "sacrifice": "Delayed sacrifice", "bounce": "Delayed return to hand"}[followup.departure]
    effect = {"op": RESOLVE_OBJECT_ACTION, "schema_version": LINKED_DEPARTURE_VERSION, "action": followup.departure,
        "object": {"ref": card.ref, "object_id": card.object_id, "zone_change_counter": card.zone_change_counter}}
    schedule_delayed_trigger(host, controller=actor, label=label, event_kind="step.begin", condition=condition,
        stack_template={"label": label, "context": {"dynamic_effects": [effect]}},
        source_object_id=source.object_id, source_logical_object_id=source.logical_object_id, once=True)
