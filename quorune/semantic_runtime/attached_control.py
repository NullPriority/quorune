from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..continuous_effect_model import (ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin,
    ContinuousEffectRelation, ContinuousOperation, Layer)
from ..rules.attached_control import AttachedControlSpec, ATTACHED_CONTROL_CAPABILITY, ATTACHED_CONTROL_HANDLER_ID
from .context import SemanticNodeError
from .continuous_components import ContinuousEffectSourceContext


@dataclass(frozen=True, slots=True)
class AttachedControlHandler:
    handler_id: str = ATTACHED_CONTROL_HANDLER_ID
    family: str = ATTACHED_CONTROL_CAPABILITY
    schema_version: int = 1
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str,...] = ('303.4e','611.3b','613.1b','613.7e','613.8')
    capability_dependencies: tuple[str,...] = (ATTACHED_CONTROL_CAPABILITY,)

    def validate(self, descriptor: Mapping[str,Any]) -> None:
        try:AttachedControlSpec.from_descriptor(descriptor)
        except ValueError as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor: Mapping[str,Any], context: ContinuousEffectSourceContext):
        self.validate(descriptor)
        if context.attached_object is None:return ()
        return (ContinuousEffect(effect_id=f'{context.source_logical_object_id}:{context.component_id}',source_id=context.source_object_id,
            layer=Layer.CONTROL,sublayer='2',timestamp=context.source_timestamp,
            operations=(ContinuousOperation('set_controller',context.source_controller),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,duration=ContinuousEffectDuration.WHILE_SOURCE_PRESENT,
            relation=ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT,related_object=context.attached_object),)
