from __future__ import annotations

"""Typed conditional timing permission and its distinct real-keyword effect."""

from dataclasses import dataclass

from ..cast_timing import CastTimingPermission
from ..continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionKind
from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation,
    ContinuousObjectIdentity, ContinuousOperation, Layer,
)
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .conditional_continuous import validate_static_public_state_condition
from .context import SemanticNodeError


CONDITIONAL_FLASH_CAPABILITY = 'timing.cast.conditional_self_flash'
CONDITIONAL_FLASH_PERMISSION_HANDLER = 'ability.static.conditional-flash.v1'
CONDITIONAL_FLASH_KEYWORD_HANDLER = 'continuous.ability.conditional-flash.v1'


def conditional_flash_condition(descriptor, *, handler_id, event):
    exact_fields(descriptor, {'handler_id','schema_version','event','source_condition'}, field='conditional Flash')
    if (descriptor['handler_id'] != handler_id or type(descriptor['schema_version']) is not int
            or descriptor['schema_version'] != 1 or descriptor['event'] != event):
        raise SemanticNodeError('Conditional Flash identity changed')
    condition = FixedPublicStateConditionSpec.from_dict(descriptor['source_condition'])
    leaves = condition.leaf_conditions()
    if any(leaf.kind in {FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
            FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY} for leaf in leaves):
        raise SemanticNodeError('Conditional self timing requires caster-relative public facts')
    if condition.terms and any(leaf.kind is FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS
            for leaf in leaves):
        raise SemanticNodeError('Compound numeric timing conditions need their full characteristic owner')
    return condition


@dataclass(frozen=True, slots=True)
class ConditionalFlashPermissionContext:
    condition_matches: bool

    def __post_init__(self):
        if type(self.condition_matches) is not bool:
            raise SemanticNodeError('Conditional Flash requires a sealed current verdict')


@dataclass(frozen=True, slots=True)
class ConditionalFlashPermissionHandler:
    handler_id: str = CONDITIONAL_FLASH_PERMISSION_HANDLER
    schema_version: int = 1
    family: str = 'ability.static.conditional_flash'
    event: str = 'cast.permission'
    rule_references: tuple[str,...] = ('109.5','113.6e','601.2e','601.2f','601.2g','601.3d','702.8a')
    capability_dependencies: tuple[str,...] = (
        CONDITIONAL_FLASH_CAPABILITY, 'timing.cast.printed_flash', 'continuous.characteristics.fixed_public_state',
    )

    def validate(self, descriptor):
        return conditional_flash_condition(descriptor,handler_id=self.handler_id,event=self.event)

    def lower(self, descriptor, context):
        self.validate(descriptor)
        if not isinstance(context,ConditionalFlashPermissionContext):
            raise SemanticNodeError('Conditional Flash requires current caster information')
        return (CastTimingPermission(),) if context.condition_matches else ()


@dataclass(frozen=True, slots=True)
class ConditionalFlashKeywordHandler:
    handler_id: str = CONDITIONAL_FLASH_KEYWORD_HANDLER
    schema_version: int = 1
    family: str = 'continuous.ability.conditional_flash'
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str,...] = ('109.5','113.6','113.6e','611.3a','613.1f','702.8a')
    capability_dependencies: tuple[str,...] = (
        CONDITIONAL_FLASH_CAPABILITY, 'continuous.characteristics.fixed_public_state',
    )

    def validate(self, descriptor):
        conditional_flash_condition(descriptor,handler_id=self.handler_id,event=self.event)
        return validate_static_public_state_condition(descriptor['source_condition'])

    def lower(self, descriptor, context):
        condition = self.validate(descriptor)
        if context.public_state is None:
            raise SemanticNodeError('Conditional Flash keyword requires current public facts')
        if not condition.matches(context.public_state):
            return ()
        if not context.source_logical_object_id:
            raise SemanticNodeError('Conditional Flash keyword requires its source incarnation')
        return (ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}:6',
            source_id=context.source_object_id,layer=Layer.ABILITY,sublayer='6',timestamp=context.source_timestamp,
            operations=(ContinuousOperation('add_ability','Flash'),),origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=ObjectQuerySpec(),relation=ContinuousEffectRelation.SOURCE_OBJECT,
            related_object=ContinuousObjectIdentity(context.source_object_id,context.source_logical_object_id)),)
