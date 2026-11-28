from __future__ import annotations

"""Instantiate one fixed public query from the source's retained designation."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from ..ability_fragments import ProtectionQualityKind, ProtectionSpec, ability_fragment_to_dict
from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation,
    ContinuousObjectIdentity, ContinuousOperation, Layer,
)
from ..entry_designations import (
    CHOSEN_CHARACTERISTICS_CAPABILITY,
    CHOSEN_CHARACTERISTICS_HANDLER_ID,
    EntryDesignationKind,
    LINKED_COLOR_PROTECTION_CAPABILITIES,
    LINKED_COLOR_PROTECTION_HANDLER_ID,
    LINKED_PLAYER_PROTECTION_CAPABILITIES,
    LINKED_PLAYER_PROTECTION_HANDLER_ID,
    validate_designation,
)
from ..linked_card_type import PROTECTION_CAPABILITY, PROTECTION_HANDLER
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError

if TYPE_CHECKING:
    from .continuous_components import ContinuousEffectSourceContext


CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY = "continuous.power_toughness.chosen_source_counter_anthem"
SOURCE_COUNTER_ANTHEM_HANDLER = "continuous.anthem.source-counter-query.v3"
_SOURCE_COUNTER_CREATURES = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))


def chosen_source_counter_anthem_dependencies(descriptor: Mapping[str, Any]) -> tuple[str, ...]:
    if descriptor.get("handler_id") != CHOSEN_CHARACTERISTICS_HANDLER_ID:
        return ()
    body = descriptor.get("body")
    if not isinstance(body, Mapping) or body.get("handler_id") != SOURCE_COUNTER_ANTHEM_HANDLER:
        return ()
    ChosenCharacteristicsHandler().validate(descriptor)
    return (CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY,)


@dataclass(frozen=True, slots=True)
class ChosenCharacteristicsHandler:
    handler_id: str = CHOSEN_CHARACTERISTICS_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.characteristics.chosen_designation"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("105.1", "205.3m", "613.1f", "613.1g", "613.4c", "614.12a", "707.2")
    capability_dependencies: tuple[str, ...] = (CHOSEN_CHARACTERISTICS_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]):
        from .continuous_components import (
            FixedQueryCharacteristicGrantHandler,
            FixedQueryKeywordGrantHandler,
            FixedQueryPowerToughnessAnthemHandler,
        )
        exact_fields(
            descriptor,
            {"handler_id", "schema_version", "event", "designation", "body"},
            field="chosen characteristics",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
        ):
            raise SemanticNodeError("Chosen characteristic handler identity changed")
        try:
            kind = EntryDesignationKind(descriptor["designation"])
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError("Chosen characteristic designation is unsupported") from exc
        body = descriptor["body"]
        if not isinstance(body, Mapping):
            raise SemanticNodeError("Chosen characteristic body must be a closed descriptor")
        handlers = (
            FixedQueryCharacteristicGrantHandler(),
            FixedQueryKeywordGrantHandler(),
            FixedQueryPowerToughnessAnthemHandler(),
            FixedQueryPowerToughnessAnthemHandler(handler_id=SOURCE_COUNTER_ANTHEM_HANDLER, schema_version=3),
        )
        for handler in handlers:
            if body.get("handler_id") == handler.handler_id:
                node = handler.validate(body)
                if (
                    node.predicate.types_all != ("creature",)
                    or node.predicate.colors_all
                    or node.predicate.subtypes_all
                    or node.predicate.subtypes_any
                ):
                    raise SemanticNodeError(
                        "Chosen characteristic body requires one unqualified creature query"
                    )
                if handler.handler_id == SOURCE_COUNTER_ANTHEM_HANDLER and (
                    kind is not EntryDesignationKind.CREATURE_TYPE
                    or node.predicate != _SOURCE_COUNTER_CREATURES
                    or node.target_controller != "source_controller"
                    or node.exclude_source
                    or (node.power, node.toughness) != (1, 1)
                ):
                    raise SemanticNodeError("Chosen source counter anthem requires controlled creatures and +1/+1 scaling")
                return kind, body, handler
        raise SemanticNodeError("Chosen characteristic body owner is unsupported")

    def lower(
        self,
        descriptor: Mapping[str, Any],
        context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        kind, body, handler = self.validate(descriptor)
        value = context.source_designations.get(kind.annotation)
        if value is None:
            return ()
        try:
            validate_designation(kind, value)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        condition = dict(body["condition"])
        predicate = ObjectQuerySpec.from_dict(condition["predicate"])
        raw = predicate.to_dict()
        raw["colors_all" if kind is EntryDesignationKind.COLOR else "subtypes_all"] = [value]
        condition["predicate"] = ObjectQuerySpec.from_dict(raw).to_dict()
        return handler.lower({**body, "condition": condition}, context)


def linked_card_type_protection_descriptor() -> dict[str, Any]:
    return {"handler_id": PROTECTION_HANDLER, "schema_version": 1, "event": "characteristics.evaluate"}


def linked_card_type_protection_fragment(card_type: str) -> ProtectionSpec:
    return ProtectionSpec(ProtectionQualityKind.CARD_TYPE, card_type)


@dataclass(frozen=True, slots=True)
class LinkedCardTypeProtectionHandler:
    handler_id: str = PROTECTION_HANDLER
    schema_version: int = 1
    family: str = "continuous.characteristics.linked_card_type_protection"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("109.5", "205.2a", "607.2d", "611.3a", "613.1f", "614.12a", "702.16a")
    capability_dependencies: tuple[str, ...] = (PROTECTION_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> None:
        exact_fields(descriptor, {"handler_id", "schema_version", "event"}, field="linked card-type protection")
        if type(descriptor["schema_version"]) is not int or dict(descriptor) != linked_card_type_protection_descriptor():
            raise SemanticNodeError("Linked card-type Protection requires its closed current-source descriptor")

    def may_affect_static_components(self, descriptor: Mapping[str, Any]) -> bool:
        return False

    def lower(self, descriptor, context: ContinuousEffectSourceContext) -> tuple[ContinuousEffect, ...]:
        from .continuous_components import FixedQueryProtectionGrantHandler

        self.validate(descriptor)
        if context.linked_card_type is None:
            return ()
        grant = FixedQueryProtectionGrantHandler()
        fragment = linked_card_type_protection_fragment(context.linked_card_type)
        return grant.lower({
            "handler_id": grant.handler_id,
            "schema_version": grant.schema_version,
            "event": grant.event,
            "condition": {"target_controller": "source_controller", "exclude_source": False,
                          "predicate": _SOURCE_COUNTER_CREATURES.to_dict()},
            "modifier": {"add_abilities": ["Protection"], "add_ability_fragments": [ability_fragment_to_dict(fragment)]},
        }, context)


def linked_player_protection_descriptor() -> dict[str, Any]:
    return {"handler_id": LINKED_PLAYER_PROTECTION_HANDLER_ID,
            "schema_version": 1, "event": "characteristics.evaluate"}


def _linked_self_protection(context: ContinuousEffectSourceContext, fragment: ProtectionSpec):
    if context.source_logical_object_id is None:
        raise SemanticNodeError("Linked protection requires a source incarnation")
    return (
        ContinuousEffect(
            effect_id=f"{context.source_object_id}:{context.component_id}:6",
            source_id=context.source_object_id,
            timestamp=context.source_timestamp,
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            layer=Layer.ABILITY,
            sublayer="6",
            relation=ContinuousEffectRelation.SOURCE_OBJECT,
            related_object=ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id),
            applies=ObjectQuerySpec(zones=("battlefield",)),
            operations=(
                ContinuousOperation("add_ability", "Protection"),
                ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(fragment)),
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class LinkedPlayerProtectionHandler:
    handler_id: str = LINKED_PLAYER_PROTECTION_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.characteristics.linked_player_protection"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("607.2d", "613.1f", "614.12a", "702.16k")
    capability_dependencies: tuple[str, ...] = LINKED_PLAYER_PROTECTION_CAPABILITIES

    def validate(self, descriptor: Mapping[str, Any]) -> None:
        exact_fields(descriptor, {"handler_id", "schema_version", "event"}, field="linked player protection")
        if type(descriptor["schema_version"]) is not int or dict(descriptor) != linked_player_protection_descriptor():
            raise SemanticNodeError("Linked player protection requires its current source component")

    def may_affect_static_components(self, descriptor: Mapping[str, Any]) -> bool:
        return False

    def lower(
        self, descriptor: Mapping[str, Any], context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        self.validate(descriptor)
        if context.linked_player is None:
            return ()
        fragment = ProtectionSpec(ProtectionQualityKind.PLAYER, context.linked_player, 4)
        return _linked_self_protection(context, fragment)


def linked_color_protection_descriptor() -> dict[str, Any]:
    return {"handler_id": LINKED_COLOR_PROTECTION_HANDLER_ID,
            "schema_version": 1, "event": "characteristics.evaluate"}


@dataclass(frozen=True, slots=True)
class LinkedColorProtectionHandler:
    handler_id: str = LINKED_COLOR_PROTECTION_HANDLER_ID
    schema_version: int = 1
    family: str = "continuous.characteristics.linked_color_protection"
    event: str = "characteristics.evaluate"
    rule_references: tuple[str, ...] = ("607.2d", "613.1f", "614.12a", "702.16b", "702.16e", "702.16f")
    capability_dependencies: tuple[str, ...] = LINKED_COLOR_PROTECTION_CAPABILITIES

    def validate(self, descriptor: Mapping[str, Any]) -> None:
        exact_fields(descriptor, {"handler_id", "schema_version", "event"}, field="linked color protection")
        if type(descriptor["schema_version"]) is not int or dict(descriptor) != linked_color_protection_descriptor():
            raise SemanticNodeError("Linked color protection requires its current source component")

    def may_affect_static_components(self, descriptor: Mapping[str, Any]) -> bool:
        return False

    def lower(
        self, descriptor: Mapping[str, Any], context: ContinuousEffectSourceContext,
    ) -> tuple[ContinuousEffect, ...]:
        self.validate(descriptor)
        if context.linked_color is None:
            return ()
        color = validate_designation(EntryDesignationKind.COLOR, context.linked_color)
        return _linked_self_protection(context, ProtectionSpec(ProtectionQualityKind.COLOR, color))
