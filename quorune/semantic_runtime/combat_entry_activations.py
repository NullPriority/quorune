from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..cast_lifecycles import FIXED_CAST_LIFECYCLE_CAPABILITY_ID
from ..combat_entry_activations import (
    CombatEntryActivationError,
    FixedNinjutsuSpec,
    FixedEncoreSpec,
    ENCORE_ABILITY_HANDLER_ID,
    ENCORE_EFFECT_HANDLER_ID,
    ENCORE_EFFECT_OPERATION,
    EncoreTokensIntent,
    FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,
    NINJUTSU_ABILITY_HANDLER_ID,
    NINJUTSU_EFFECT_HANDLER_ID,
    NINJUTSU_EFFECT_OPERATION,
    NinjutsuEntryIntent,
)
from ..replacement.immutable import FrozenMap
from ..rules.capabilities import load_default_capability_registry
from .component_registry import RuntimeComponentRegistry, exact_fields
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class FixedNinjutsuAbilityHandler:
    handler_id: str = NINJUTSU_ABILITY_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.ninjutsu"
    event: str = "activate"
    rule_references: tuple[str, ...] = (
        "602.1",
        "602.2",
        "702.49",
        "702.49a",
        "702.49b",
        "702.49c",
        "702.49d",
    )
    capability_dependencies: tuple[str, ...] = (
        FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,
    )

    def validate(self, descriptor: Mapping[str, Any]) -> FixedNinjutsuSpec:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
                "ability",
            },
            field="fixed Ninjutsu handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
            or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True
            or not isinstance(descriptor["ability"], Mapping)
        ):
            raise SemanticNodeError("Fixed Ninjutsu handler is malformed")
        try:
            return FixedNinjutsuSpec.from_dict(descriptor["ability"])
        except CombatEntryActivationError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self, descriptor: Mapping[str, Any], context: object
    ) -> tuple[FixedNinjutsuSpec, ...]:
        del context
        return (self.validate(descriptor),)


class FixedNinjutsuAbilityRegistry(
    RuntimeComponentRegistry[object, FixedNinjutsuSpec]
):
    pass


@lru_cache(maxsize=1)
def default_fixed_ninjutsu_ability_registry() -> FixedNinjutsuAbilityRegistry:
    registry = FixedNinjutsuAbilityRegistry((FixedNinjutsuAbilityHandler(),))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


def fixed_ninjutsu_specs_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[FixedNinjutsuSpec, ...]:
    registry = default_fixed_ninjutsu_ability_registry()
    result: list[FixedNinjutsuSpec] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        result.extend(registry.lower(descriptor, None))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class NinjutsuEntryEffectHandler:
    operation: str = NINJUTSU_EFFECT_OPERATION
    handler_id: str = NINJUTSU_EFFECT_HANDLER_ID
    schema_version: int = 1
    family: str = "combat.entry.ninjutsu"
    rule_references: tuple[str, ...] = ("702.49", "702.49a")
    capability_dependencies: tuple[str, ...] = (
        FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        if frozenset(effect) not in {
            frozenset({"op", "attack_target"}),
            frozenset({"op", "attack_target", "_replacement_selections"}),
        } or effect.get("op") != self.operation:
            raise SemanticNodeError("Ninjutsu entry effect is malformed")
        source = context.source
        target = effect.get("attack_target")
        if (
            source is None
            or not all(
                (
                    source.stack_ref,
                    source.object_id,
                    source.logical_object_id,
                    source.card_ref,
                )
            )
            or not isinstance(target, Mapping)
        ):
            raise SemanticNodeError(
                "Ninjutsu entry requires source and attack-recipient identity"
            )
        selections = effect.get("_replacement_selections") or ()
        if not isinstance(selections, (list, tuple)):
            raise SemanticNodeError(
                "Ninjutsu replacement selections must be an array"
            )
        try:
            intent = NinjutsuEntryIntent(
                actor=context.actor,
                stack_ref=source.stack_ref,
                source_object_id=str(source.object_id),
                source_ref=str(source.card_ref),
                source_logical_object_id=str(source.logical_object_id),
                attack_target=FrozenMap(target),
                replacement_selections=tuple(selections),
            )
        except CombatEntryActivationError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


NINJUTSU_EFFECT_HANDLERS = (NinjutsuEntryEffectHandler(),)


@dataclass(frozen=True, slots=True)
class FixedEncoreAbilityHandler:
    handler_id: str = ENCORE_ABILITY_HANDLER_ID
    schema_version: int = 1
    family: str = "ability.activated.encore"
    event: str = "activate"
    rule_references: tuple[str, ...] = ("602.1", "602.2", "702.141a")
    capability_dependencies: tuple[str, ...] = (
        FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,
    )

    def validate(self, descriptor: Mapping[str, Any]) -> FixedEncoreSpec:
        exact_fields(
            descriptor,
            {
                "handler_id",
                "schema_version",
                "event",
                REQUIRES_COMPLETE_CARD_PROGRAM_FIELD,
                "ability",
            },
            field="fixed Encore handler",
        )
        if (
            descriptor["handler_id"] != self.handler_id
            or descriptor["schema_version"] != self.schema_version
            or descriptor["event"] != self.event
            or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True
            or not isinstance(descriptor["ability"], Mapping)
        ):
            raise SemanticNodeError("Fixed Encore handler is malformed")
        try:
            return FixedEncoreSpec.from_dict(descriptor["ability"])
        except CombatEntryActivationError as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(
        self, descriptor: Mapping[str, Any], context: object
    ) -> tuple[FixedEncoreSpec, ...]:
        del context
        return (self.validate(descriptor),)


class FixedEncoreAbilityRegistry(
    RuntimeComponentRegistry[object, FixedEncoreSpec]
):
    pass


@lru_cache(maxsize=1)
def default_fixed_encore_ability_registry() -> FixedEncoreAbilityRegistry:
    registry = FixedEncoreAbilityRegistry((FixedEncoreAbilityHandler(),))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


def fixed_encore_specs_from_descriptors(
    descriptors: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[FixedEncoreSpec, ...]:
    registry = default_fixed_encore_ability_registry()
    result: list[FixedEncoreSpec] = []
    for descriptor in descriptors:
        if registry.describe(str(descriptor.get("handler_id") or "")) is None:
            continue
        result.extend(registry.lower(descriptor, None))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class EncoreTokensEffectHandler:
    operation: str = ENCORE_EFFECT_OPERATION
    handler_id: str = ENCORE_EFFECT_HANDLER_ID
    schema_version: int = 1
    family: str = "token.encore"
    rule_references: tuple[str, ...] = ("702.141a",)
    capability_dependencies: tuple[str, ...] = (
        FIXED_COMBAT_ENTRY_LIFECYCLE_CAPABILITY_ID,
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        if frozenset(effect) not in {
            frozenset({"op"}),
            frozenset({"op", "_replacement_selections"}),
        } or effect.get("op") != self.operation:
            raise SemanticNodeError("Encore token effect is malformed")
        source = context.source
        if source is None or not all(
            (
                source.stack_ref,
                source.object_id,
                source.logical_object_id,
                source.card_ref,
            )
        ):
            raise SemanticNodeError("Encore requires typed source identity")
        selections = effect.get("_replacement_selections") or ()
        if not isinstance(selections, (list, tuple)):
            raise SemanticNodeError(
                "Encore replacement selections must be an array"
            )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                EncoreTokensIntent(
                    actor=context.actor,
                    stack_ref=source.stack_ref,
                    source_object_id=str(source.object_id),
                    source_ref=str(source.card_ref),
                    source_logical_object_id=str(source.logical_object_id),
                    opponents=tuple(
                        seat
                        for seat in context.query.active_seats
                        if seat != context.actor
                    ),
                    replacement_selections=tuple(selections),
                ),
            ),
        )


NINJUTSU_EFFECT_HANDLERS = (
    NinjutsuEntryEffectHandler(),
    EncoreTokensEffectHandler(),
)


__all__ = [
    "default_fixed_ninjutsu_ability_registry",
    "default_fixed_encore_ability_registry",
    "fixed_encore_specs_from_descriptors",
    "fixed_ninjutsu_specs_from_descriptors",
    "FixedNinjutsuAbilityHandler",
    "FixedNinjutsuAbilityRegistry",
    "FixedEncoreAbilityHandler",
    "FixedEncoreAbilityRegistry",
    "NINJUTSU_EFFECT_HANDLERS",
    "NinjutsuEntryEffectHandler",
]
