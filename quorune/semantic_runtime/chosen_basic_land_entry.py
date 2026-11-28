from __future__ import annotations

"""Typed intrinsic entry choice and its linked layer-four type setting."""

from dataclasses import dataclass

from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation,
    ContinuousObjectIdentity, ContinuousOperation, Layer,
)
from ..entry_designations import (
    BASIC_LAND_ENTRY_CAPABILITIES, BASIC_LAND_ENTRY_CAPABILITY, BASIC_LAND_ENTRY_HANDLER_ID,
    BASIC_LAND_ENTRY_LIFE_FIELD, BASIC_LAND_ENTRY_PAID_FIELD, CHOSEN_BASIC_LAND_HANDLER_ID,
    EntryDesignationKind, validate_designation,
)
from ..life_state import available_life_for_payment
from ..object_predicate import ObjectQuerySpec
from ..replacement import AddAmount, ReplacementClass, ReplacementEffect, SetField
from .component_registry import exact_fields
from .context import SemanticNodeError
from .entry_designations import EntryDesignationHandler


_RULES = ("119.4", "305.6", "305.7", "400.7", "607.2d", "613.1d", "614.12", "616.1", "707.2")


@dataclass(frozen=True, slots=True)
class ChosenBasicLandEntryHandler(EntryDesignationHandler):
    handler_id: str = BASIC_LAND_ENTRY_HANDLER_ID
    capability_dependencies: tuple[str, ...] = BASIC_LAND_ENTRY_CAPABILITIES
    rule_references: tuple[str, ...] = _RULES

    def validate(self, descriptor):
        exact_fields(
            descriptor, {"handler_id", "schema_version", "event", "designation", "life",
                         "requires_complete_card_program"}, field="basic land entry choice",
        )
        life = descriptor["life"]
        if (descriptor["designation"] != "basic_land_type" or type(life) is not int or life <= 0
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("Basic land entry requires its linked type and positive life cost")
        return EntryDesignationHandler.validate(self, {
            key: descriptor[key] for key in ("handler_id", "schema_version", "event", "designation")
        })

    def subject_replacement_effects(self, descriptor, *, subject, component_id, host, simultaneous_departures=()):
        kind = self.validate(descriptor)
        controller = subject.destination_controller
        if "land" not in subject.object_types:
            return ()
        if host is None or controller not in host.active_seats or not component_id:
            raise SemanticNodeError("Basic land entry requires its captured controller and component")
        limit = max(0, available_life_for_payment(host, controller))
        choices = tuple(self._designation_effect(
            subject, component_id, kind, value,
            followups=(SetField(BASIC_LAND_ENTRY_LIFE_FIELD, limit),),
        ) for value in kind.values)
        life = descriptor["life"]
        complete = SetField(BASIC_LAND_ENTRY_PAID_FIELD, True)
        payment = ReplacementEffect(
            effect_id=f"{self.handler_id}:{subject.object_ref}:{component_id}:pay",
            source_id=subject.object_ref, event_kind=self.event, replacement_class=ReplacementClass.OTHER,
            conditions={
                "destination": {"eq": "battlefield"}, "object_ref": {"eq": subject.object_ref},
                kind.event_field: {"in": list(kind.values)}, BASIC_LAND_ENTRY_PAID_FIELD: {"eq": None},
            },
            operations=(AddAmount("entry_life_payment", life), complete),
            decline_operations=(SetField("tapped", True), complete), optional=True,
            label=f"Pay {life} life", decline_label="Enter tapped without paying life",
        )
        return (*choices, payment)


@dataclass(frozen=True, slots=True)
class ChosenBasicLandTypeHandler:
    handler_id: str = CHOSEN_BASIC_LAND_HANDLER_ID
    schema_version: int = 1
    family: str = BASIC_LAND_ENTRY_CAPABILITY
    event: str = "characteristics.evaluate"
    # Its own type setting removes printed abilities in layer four.
    source_component_layer: Layer = Layer.TEXT
    capability_dependencies: tuple[str, ...] = BASIC_LAND_ENTRY_CAPABILITIES
    rule_references: tuple[str, ...] = _RULES

    def validate(self, descriptor):
        exact_fields(
            descriptor, {"handler_id", "schema_version", "event", "requires_complete_card_program"},
            field="linked chosen basic land type setting",
        )
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("Basic land type setting needs its complete linked printed source")

    def lower(self, descriptor, context):
        self.validate(descriptor)
        kind = EntryDesignationKind.BASIC_LAND_TYPE
        value = context.source_designations.get(kind.annotation)
        if value is None:
            return ()
        try:
            subtype = validate_designation(kind, value)
            identity = ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return (ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}:4:chosen-basic-type",
            source_id=context.source_object_id, layer=Layer.TYPE, sublayer="4",
            timestamp=context.source_timestamp, origin=ContinuousEffectOrigin.STATIC_ABILITY,
            operations=(ContinuousOperation("set_basic_land_types", (subtype,)),),
            applies=ObjectQuerySpec(zones=("battlefield",), types_all=("land",)),
            relation=ContinuousEffectRelation.SOURCE_OBJECT, related_object=identity,
        ),)
