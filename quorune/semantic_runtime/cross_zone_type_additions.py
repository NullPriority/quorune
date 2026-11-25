from __future__ import annotations

"""Layer-four additions with separate controlled-spell and owned-card scopes."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousObjectIdentity,
    ContinuousOperation, Layer,
)
from ..entry_designations import (
    ENTRY_DESIGNATION_CAPABILITY, SELF_CHOSEN_SUBTYPE_CAPABILITY, SELF_CHOSEN_SUBTYPE_CONTINUOUS_HANDLER_ID,
    EntryDesignationKind, validate_designation,
)
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError


if TYPE_CHECKING:
    from .continuous_components import ContinuousEffectSourceContext


CROSS_ZONE_TYPE_ADDITION_HANDLER = "continuous.characteristics.cross-zone-type-addition.v1"
CROSS_ZONE_TYPE_ADDITION_CAPABILITY = "continuous.characteristics.cross_zone_type_addition"
CROSS_ZONE_CHOSEN_CREATURE_TYPE_HANDLER = "continuous.characteristics.cross-zone-chosen-creature-type.v1"
CROSS_ZONE_CHOSEN_CREATURE_TYPE_CAPABILITY = "continuous.characteristics.cross_zone_chosen_creature_type"
CONTROLLED_CHOSEN_CREATURE_SUBTYPE_HANDLER = "continuous.characteristics.controlled-chosen-subtype.v1"
CONTROLLED_CHOSEN_CREATURE_SUBTYPE_CAPABILITY = "continuous.characteristics.controlled_chosen_creature_subtype"
_OWNED_CARD_ZONES = ("command", "exile", "graveyard", "hand", "library", "stack")
_PERMANENT_SPELL_TYPES = tuple(sorted(PERMANENT_CARD_TYPES - {"land"}))


@dataclass(frozen=True, slots=True)
class CrossZoneTypeAdditionSpec:
    added_type: str
    receiver: str
    includes_owned_cards: bool

    def __post_init__(self) -> None:
        if (type(self.added_type) is not str or self.added_type not in {"artifact", "enchantment"}
                or type(self.receiver) is not str or self.receiver not in {"creatures", "nonland_permanents"}
                or type(self.includes_owned_cards) is not bool):
            raise ValueError("Cross-zone type additions require closed type and receiver scopes")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CrossZoneTypeAdditionSpec:
        exact_fields(value, {"added_type", "receiver", "includes_owned_cards"}, field="cross-zone type addition")
        return cls(value["added_type"], value["receiver"], value["includes_owned_cards"])

    def to_dict(self) -> dict[str, Any]:
        return {"added_type": self.added_type, "receiver": self.receiver,
                "includes_owned_cards": self.includes_owned_cards}

    def recipients(self, controller: str) -> tuple[ObjectQuerySpec, ...]:
        return _cross_zone_recipients(self.receiver, controller, self.includes_owned_cards)


def _cross_zone_recipients(receiver: str, controller: str, includes_owned_cards: bool) -> tuple[ObjectQuerySpec, ...]:
    if receiver == "creatures":
        permanent = spell = owned = {"types_all": ("creature",)}
    else:
        permanent = {"excluded_types": ("land",)}
        spell = {"types_any": _PERMANENT_SPELL_TYPES}
        owned = {**spell, "excluded_types": ("land",)}
    controlled = (
        ObjectQuerySpec(zones=("battlefield",), controller=controller, **permanent),
        ObjectQuerySpec(zones=("stack",), controller=controller, **spell),
    )
    if not includes_owned_cards:
        return controlled
    return (*controlled, ObjectQuerySpec(zones=_OWNED_CARD_ZONES, owner=controller, token=False, **owned))


def _type_addition_effects(
    context: ContinuousEffectSourceContext,
    operation: ContinuousOperation,
    recipients: tuple[ObjectQuerySpec, ...],
) -> tuple[ContinuousEffect, ...]:
    return tuple(
        ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}:4:{index}",
            source_id=context.source_object_id, layer=Layer.TYPE, sublayer="4",
            timestamp=context.source_timestamp, operations=(operation,),
            origin=ContinuousEffectOrigin.STATIC_ABILITY, applies=predicate,
        )
        for index, predicate in enumerate(recipients)
    )


@dataclass(frozen=True, slots=True)
class CrossZoneTypeAdditionHandler:
    handler_id: str = CROSS_ZONE_TYPE_ADDITION_HANDLER
    schema_version: int = 1
    family: str = CROSS_ZONE_TYPE_ADDITION_CAPABILITY
    event: str = "characteristics.evaluate"
    source_component_layer: Layer = Layer.TYPE
    rule_references: tuple[str, ...] = ("109.2a", "109.2b", "110.4a", "110.4b", "205.1b", "611.3b", "613.1d")
    capability_dependencies: tuple[str, ...] = (CROSS_ZONE_TYPE_ADDITION_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> CrossZoneTypeAdditionSpec:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "requires_complete_card_program", "addition"},
                     field="cross-zone type addition component")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("Cross-zone type additions require their exact handler and complete source")
        return CrossZoneTypeAdditionSpec.from_dict(descriptor["addition"])

    def lower(self, descriptor, context):
        spec = self.validate(descriptor)
        operation = ContinuousOperation("add_types", (spec.added_type.title(),), field="card_types")
        return _type_addition_effects(context, operation, spec.recipients(context.source_controller))


@dataclass(frozen=True, slots=True)
class CrossZoneChosenCreatureTypeHandler:
    handler_id: str = CROSS_ZONE_CHOSEN_CREATURE_TYPE_HANDLER
    schema_version: int = 1
    family: str = CROSS_ZONE_CHOSEN_CREATURE_TYPE_CAPABILITY
    event: str = "characteristics.evaluate"
    source_component_layer: Layer = Layer.TYPE
    rule_references: tuple[str, ...] = ("109.2a", "109.2b", "205.1b", "205.3m", "611.3b", "613.1d", "614.12a")
    capability_dependencies: tuple[str, ...] = (CROSS_ZONE_CHOSEN_CREATURE_TYPE_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> None:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "requires_complete_card_program"},
                     field="cross-zone chosen creature type component")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("A cross-zone chosen creature type requires its exact handler and complete source")

    def lower(
        self, descriptor: Mapping[str, Any], context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        self.validate(descriptor)
        kind = EntryDesignationKind.CREATURE_TYPE
        choice = context.source_designations.get(kind.annotation)
        if choice is None:
            return ()
        try:
            subtype = validate_designation(kind, choice)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        operation = ContinuousOperation("add_types", (subtype.title(),), field="subtypes")
        return _type_addition_effects(context, operation, self._recipients(context))

    def _recipients(self, context):
        return _cross_zone_recipients("creatures", context.source_controller, includes_owned_cards=True)


@dataclass(frozen=True, slots=True)
class ControlledChosenCreatureSubtypeHandler(CrossZoneChosenCreatureTypeHandler):
    handler_id: str = CONTROLLED_CHOSEN_CREATURE_SUBTYPE_HANDLER
    family: str = CONTROLLED_CHOSEN_CREATURE_SUBTYPE_CAPABILITY
    rule_references: tuple[str, ...] = ("205.1b", "205.3m", "611.3b", "613.1d", "614.12", "614.12a")
    capability_dependencies: tuple[str, ...] = (
        ENTRY_DESIGNATION_CAPABILITY, CONTROLLED_CHOSEN_CREATURE_SUBTYPE_CAPABILITY,
    )

    def _recipients(self, context):
        return (ObjectQuerySpec(zones=("battlefield",), controller=context.source_controller,
                                types_all=("creature",)),)



@dataclass(frozen=True, slots=True)
class SelfChosenCreatureSubtypeHandler:
    handler_id: str = SELF_CHOSEN_SUBTYPE_CONTINUOUS_HANDLER_ID
    schema_version: int = 1
    family: str = SELF_CHOSEN_SUBTYPE_CAPABILITY
    event: str = "characteristics.evaluate"
    source_component_layer: Layer = Layer.TYPE
    rule_references: tuple[str, ...] = ("205.1b", "205.3d", "205.3m", "611.3b", "613.1d", "614.12")
    capability_dependencies: tuple[str, ...] = (ENTRY_DESIGNATION_CAPABILITY, SELF_CHOSEN_SUBTYPE_CAPABILITY)

    def validate(self, descriptor: Mapping[str, Any]) -> None:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "requires_complete_card_program"},
                     field="self chosen creature subtype")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("Self chosen subtype requires the complete linked printed source")

    def lower(self, descriptor, context: ContinuousEffectSourceContext):
        self.validate(descriptor)
        kind = EntryDesignationKind.CREATURE_TYPE
        choice = context.source_designations.get(kind.annotation)
        if choice is None:
            return ()
        try:
            subtype = validate_designation(kind, choice)
            identity = ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return (ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}:4:chosen-type",
            source_id=context.source_object_id, layer=Layer.TYPE, sublayer="4",
            timestamp=context.source_timestamp, origin=ContinuousEffectOrigin.STATIC_ABILITY,
            operations=(ContinuousOperation("add_types", (subtype.title(),), field="subtypes"),),
            applies=ObjectQuerySpec(zones=("battlefield",), types_any=("creature", "kindred")),
            relation=ContinuousEffectRelation.SOURCE_OBJECT, related_object=identity,
        ),)
