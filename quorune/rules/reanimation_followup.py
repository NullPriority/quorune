from __future__ import annotations

"""Special reanimation entry riders and their linked battlefield departures."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from ..continuous_effect_model import ContinuousObjectIdentity
from ..effect_contracts import REANIMATE_OPERATION
from ..delayed_object_model import RESOLVE_OBJECT_ACTION
from ..linked_departure_exile_model import ResolvedLinkedDepartureExile
from ..replacement_effects import ReplacementClass, ReplacementEffect, SetField


REANIMATION_FOLLOWUP_CAPABILITY = "zone.reanimate.fixed_haste_and_departure"
REANIMATION_FOLLOWUP_MECHANIC = "fixed-target-reanimation-haste-and-departure"
REANIMATION_FOLLOWUP_VERSION = 4
REANIMATION_EXILE_REPLACEMENT_VERSION = 5
REANIMATION_SACRIFICE_ONLY_VERSION = 8
REANIMATION_FOLLOWUP_VERSIONS = (
    REANIMATION_FOLLOWUP_VERSION, REANIMATION_EXILE_REPLACEMENT_VERSION, REANIMATION_SACRIFICE_ONLY_VERSION,
)
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
    exile_on_leave: bool = False

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
        if type(self.exile_on_leave) is not bool or self.exile_on_leave and (
            self.haste_duration, self.departure, self.end_step, self.power_boost,
        ) != ("zone_object", "exile", "next", 0):
            raise ValueError("A leave replacement requires the complete next-end-step exile instruction")

    def to_dict(self):
        result = asdict(self)
        if not self.exile_on_leave:
            del result["exile_on_leave"]
        return result

    @classmethod
    def from_dict(cls, value, *, exile_on_leave=False):
        fields = {
            "haste_duration", "departure", "end_step", "power_boost",
        }
        if exile_on_leave:
            fields.add("exile_on_leave")
        if (not isinstance(value, Mapping) or set(value) != fields
                or exile_on_leave and value["exile_on_leave"] is not True):
            raise ValueError("Return follow-up fields are closed")
        return cls(**value)


class DelayedReanimationSacrifice:
    """The exact delayed sacrifice with no battlefield-entry modifier."""

    __slots__ = ()
    departure = "sacrifice"
    end_step = "next"

    def to_dict(self):
        return {"departure": self.departure, "end_step": self.end_step}

    @classmethod
    def from_dict(cls, value):
        spec = cls()
        if not isinstance(value, Mapping) or dict(value) != spec.to_dict():
            raise ValueError("Delayed reanimation sacrifice requires its exact next-end-step result")
        return spec

    @staticmethod
    def target_schema():
        return {
            "zones": ["graveyard"], "categories": ["card"], "owner_relation": "you",
            "types_any": ["creature"], "count": 1,
        }


def validate_reanimation_followup(effect, *, materialized=False):
    fields = _REANIMATION_FIELDS | ({"target_snapshot"} if materialized else set())
    allowed = fields | (_RUNTIME_FIELDS if materialized else set())
    if (not isinstance(effect, Mapping) or not fields <= set(effect) <= allowed
        or effect["op"] != REANIMATE_OPERATION or type(effect["schema_version"]) is not int
        or effect["schema_version"] not in REANIMATION_FOLLOWUP_VERSIONS or type(effect["tapped"]) is not bool):
        raise ValueError("Linked reanimation requires a complete versioned instruction")
    if materialized:
        _validate_subject(effect["target_snapshot"])
        if (effect["target_snapshot"]["ref"] != effect["card"]
            or type(effect["controller"]) is not str or not effect["controller"]):
            raise ValueError("Linked reanimation requires a bound target and controller")
    elif effect["card"] != "$target.0" or effect["controller"] not in ("$controller", "$target.owner.0"):
        raise ValueError("Linked reanimation requires its declared target and controller")
    if effect["schema_version"] == REANIMATION_SACRIFICE_ONLY_VERSION:
        if effect["tapped"] or not materialized and effect["controller"] != "$controller":
            raise ValueError("Delayed sacrifice requires the exact untapped return under the spell controller")
        return DelayedReanimationSacrifice.from_dict(effect["followup"])
    return BattlefieldEntryFollowup.from_dict(effect["followup"],
        exile_on_leave=effect["schema_version"] == REANIMATION_EXILE_REPLACEMENT_VERSION)


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


def battlefield_entry_followup_capabilities(followup):
    common = ("trigger.delayed.fixed_object_action", "trigger.placement.apnap", "zone.change.destination_replacement")
    if isinstance(followup, DelayedReanimationSacrifice):
        return (*common, "choice.affected_player.fixed_sacrifice")
    duration = ("continuous.resolution.fixed_keyword_zone_object" if followup.haste_duration == "zone_object"
        else "continuous.resolution.fixed_characteristics_until_end_of_turn")
    departure = {"exile": "permanent.exile.effect", "sacrifice": "choice.affected_player.fixed_sacrifice",
        "bounce": "permanent.return.owner_hand"}[followup.departure]
    return (*common, "combat.attack.haste", "activation.tap_untap_cost.haste", duration, departure)


def linked_departure_exile_replacements(state):
    for effect in state.continuous_effects or ():
        if not isinstance(effect, ResolvedLinkedDepartureExile):
            continue
        subject = effect.subject
        card = state.cards.get(subject.object_id)
        if card is None or card.zone != "battlefield" or card.phased_out or card.logical_object_id != subject.logical_object_id:
            continue
        yield ReplacementEffect(effect_id=effect.effect_id, source_id=effect.source_id,
            event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
            conditions={"origin": {"eq": "battlefield"}, "destination": {"not_in": ["exile"]},
                        "logical_object_id": {"eq": subject.logical_object_id}},
            operations=(SetField("destination", "exile"),), label="Exile the returned permanent instead")


def reanimation_entry_handler(effect):
    """Select an entry variant with its own subject-binding authority."""
    from .opposing_death_delayed_return import reanimation_death_occurrence_owner
    from .ordered_graveyard_reanimation import apply_ordered_graveyard_reanimation, is_ordered_graveyard_reanimation
    from .enchanted_departure_return import apply_enchanted_departure_return, is_enchanted_departure_return
    from .granted_death_role_return import (
        apply_granted_death_role_return, is_granted_death_role_return,
        apply_granted_self_death_return, is_granted_self_death_return,
    )

    if is_granted_self_death_return(effect):
        return apply_granted_self_death_return
    if is_granted_death_role_return(effect):
        return apply_granted_death_role_return
    if is_ordered_graveyard_reanimation(effect):
        return apply_ordered_graveyard_reanimation
    if is_enchanted_departure_return(effect):
        return apply_enchanted_departure_return
    return reanimation_death_occurrence_owner(effect)


def _commit_reanimation_entry_modifiers(host, card, source, followup):
    from ..continuous_effect_state import (
        ContinuousEffectDuration, ContinuousOperation, Layer, ResolutionContinuousComponent,
        commit_continuous_effect, create_resolution_continuous_effect_components,
    )

    components = [ResolutionContinuousComponent(Layer.ABILITY, "6", (ContinuousOperation("add_ability", "Haste"),))]
    if followup.power_boost:
        components.append(ResolutionContinuousComponent(Layer.POWER_TOUGHNESS, "7c",
            (ContinuousOperation("modify_power_toughness", (followup.power_boost, 0)),)))
    create_resolution_continuous_effect_components(host, source=source, targets=(card,),
        components=tuple(components), duration=ContinuousEffectDuration(followup.haste_duration))
    if followup.exile_on_leave:
        commit_continuous_effect(host.state, ResolvedLinkedDepartureExile(
            effect_id=host._next_ref("RX"), source_id=source.card_ref or source.stack_ref,
            subject=ContinuousObjectIdentity(card.object_id, card.logical_object_id),
        ))

def commit_battlefield_entry_followup(
    host, card, *, previous_zone_counter, actor, source, followup, schedule_without_arrival=False,
):
    entered = (card is not None and card.zone == "battlefield"
               and not card.phased_out and card.zone_change_counter == previous_zone_counter + 1)
    if not entered and not schedule_without_arrival:
        return
    from ..trigger_processing import schedule_delayed_trigger

    if entered and isinstance(followup, BattlefieldEntryFollowup):
        _commit_reanimation_entry_modifiers(host, card, source, followup)
    condition = {"phase": "ending", "step": "end_step"}
    if followup.end_step == "controllers_next":
        condition["player"] = "controller"
    label = {"exile": "Delayed exile", "sacrifice": "Delayed sacrifice", "bounce": "Delayed return to hand"}[followup.departure]
    effects = []
    if entered:
        subject = {"ref": card.ref, "object_id": card.object_id, "zone_change_counter": card.zone_change_counter}
        effects.append({"op": RESOLVE_OBJECT_ACTION, "schema_version": LINKED_DEPARTURE_VERSION,
                        "action": followup.departure, "object": subject})
    schedule_delayed_trigger(host, controller=actor, label=label, event_kind="step.begin", condition=condition,
        stack_template={"label": label, "context": {"dynamic_effects": effects}},
        source_object_id=source.object_id, source_logical_object_id=source.logical_object_id, once=True)
