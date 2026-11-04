from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..rules.capabilities import load_default_capability_registry
from ..self_zone_move import (
    SelfZoneMoveError,
    SelfZoneMoveIntent,
    SelfZoneMoveSpec,
    SELF_ZONE_MOVE_ABILITY_HANDLER_ID,
    SELF_ZONE_MOVE_CAPABILITY_ID,
    SELF_ZONE_MOVE_EFFECT_HANDLER_ID,
    SELF_ZONE_MOVE_OPERATION,
)
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class SelfZoneMoveAbilityHandler:
    handler_id: str = SELF_ZONE_MOVE_ABILITY_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.self_zone_move"
    event: str = "activate"
    rule_references: tuple[str, ...] = ("400.7", "602.1", "602.2", "701.23")
    capability_dependencies: tuple[str, ...] = (SELF_ZONE_MOVE_CAPABILITY_ID,)

    def validate(self, descriptor: Mapping[str, Any]) -> SelfZoneMoveSpec:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
                "move",
            },
            field="self-zone-move ability handler",
        )
        if descriptor["handler_id"] != self.handler_id or descriptor["event"] != self.event:
            raise SemanticNodeError("Self-zone-move handler identity changed")
        if descriptor["schema_version"] != self.schema_version:
            raise SemanticNodeError("Unsupported self-zone-move handler schema")
        value = descriptor["move"]
        if not isinstance(value, Mapping):
            raise SemanticNodeError("Self-zone-move descriptor must be an object")
        try:
            spec = SelfZoneMoveSpec.from_dict(value)
        except SelfZoneMoveError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not spec.requires_complete_card_program:
            raise SemanticNodeError("Self-zone complete-card policy changed")
        return spec

    def lower(self, descriptor: Mapping[str, Any], context: object) -> tuple[SelfZoneMoveSpec, ...]:
        del context
        return (self.validate(descriptor),)


class SelfZoneMoveAbilityRegistry(RuntimeComponentRegistry[object, SelfZoneMoveSpec]):
    pass


@lru_cache(maxsize=1)
def default_self_zone_move_ability_registry() -> SelfZoneMoveAbilityRegistry:
    registry = SelfZoneMoveAbilityRegistry((SelfZoneMoveAbilityHandler(),))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


def self_zone_move_specs_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[SelfZoneMoveSpec, ...]:
    registry = default_self_zone_move_ability_registry()
    result: list[SelfZoneMoveSpec] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        result.extend(registry.lower(descriptor, None))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class SelfZoneMoveEffectHandler:
    operation: str = SELF_ZONE_MOVE_OPERATION
    handler_id: str = SELF_ZONE_MOVE_EFFECT_HANDLER_ID
    schema_version: int = 1
    family: str = "zone.self_move"
    rule_references: tuple[str, ...] = ("400.7", "608.2c", "701.23")
    capability_dependencies: tuple[str, ...] = (SELF_ZONE_MOVE_CAPABILITY_ID,)

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        from ..rules.graveyard_self_return import GRAVEYARD_SELF_RETURN_VERSION,graveyard_self_return_dependencies
        try:graveyard_self_return_dependencies(effect)
        except ValueError as exc:raise SemanticNodeError(str(exc)) from exc
        allowed = {"op", "origin", "destination", "tapped", "source_form", "_replacement_selections"}
        allowed.add(GRAVEYARD_SELF_RETURN_VERSION)
        if set(effect) - allowed or not {"op", "origin", "destination", "tapped", "source_form"}.issubset(effect):
            raise SemanticNodeError("Self-zone-move effect has an invalid shape")
        if effect["op"] != self.operation:
            raise SemanticNodeError("Self-zone-move effect operation changed")
        source = context.source
        if source is None or not all((source.stack_ref, source.object_id, source.logical_object_id, source.card_ref)):
            raise SemanticNodeError("Self-zone movement requires source identity")
        selections = effect.get("_replacement_selections") or ()
        if not isinstance(selections, (list, tuple)):
            raise SemanticNodeError("Self-zone replacement selections must be an array")
        try:
            intent = SelfZoneMoveIntent(
                actor=context.actor,
                stack_ref=str(source.stack_ref),
                object_id=str(source.object_id),
                card_ref=str(source.card_ref),
                logical_object_id=str(source.logical_object_id),
                origin=effect["origin"],
                destination=effect["destination"],
                tapped=effect["tapped"],
                source_form=effect["source_form"],
                replacement_selections=tuple(selections),
            )
        except SelfZoneMoveError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))


SELF_ZONE_MOVE_EFFECT_HANDLERS = (SelfZoneMoveEffectHandler(),)


__all__ = [
    "default_self_zone_move_ability_registry",
    "SelfZoneMoveAbilityHandler",
    "SelfZoneMoveAbilityRegistry",
    "self_zone_move_specs_from_descriptors",
    "SELF_ZONE_MOVE_EFFECT_HANDLERS",
    "SelfZoneMoveEffectHandler",
]
