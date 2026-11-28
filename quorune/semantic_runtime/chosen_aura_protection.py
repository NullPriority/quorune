from __future__ import annotations

"""One retained source designation becomes an ordinary per-instance grant."""

from dataclasses import dataclass
from ..ability_fragments import ProtectionSpec,ProtectionQualityKind,ability_fragment_to_dict
from ..continuous_effect_model import ContinuousEffect,ContinuousEffectOrigin,ContinuousEffectRelation,ContinuousOperation,Layer
from ..entry_designations import EntryDesignationKind,validate_designation
from ..object_predicate import ObjectQuerySpec
from ..protection_attachment_exceptions import (
    CAPABILITY,
    CHOSEN_AURA_PROTECTION_CAPABILITY,
    CHOSEN_AURA_PROTECTION_HANDLER,
    CONTROLLED_ATTACHMENT_PROTECTION_CAPABILITY,
    CONTROLLED_ATTACHMENT_PROTECTION_HANDLER,
    CONTROLLED_ATTACHMENT_PROTECTION_VERSION,
    ProtectionAttachmentRetention,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class SourceChosenAuraProtectionHandler:
    handler_id:str=CHOSEN_AURA_PROTECTION_HANDLER
    schema_version:int=1
    family:str='continuous.attached.source_chosen_color_protection'
    event:str='characteristics.evaluate'
    rule_references:tuple[str,...]=('105.1','105.4','400.7','614.12a','702.16n','613.1f')
    capability_dependencies:tuple[str,...]=(CHOSEN_AURA_PROTECTION_CAPABILITY,CAPABILITY)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','subject'},field='source-chosen Aura protection')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=self.schema_version or descriptor['event']!=self.event
                or descriptor['subject']!='source_attached_creature'):
            raise SemanticNodeError('Source-chosen Aura protection has an unsupported definition')
        return ObjectQuerySpec(zones=('battlefield',),types_all=('creature',))

    def lower(self,descriptor,context):
        predicate=self.validate(descriptor)
        if context.attached_object is None:return ()
        try:color=validate_designation(EntryDesignationKind.COLOR,context.source_designations.get('chosen_color'))
        except ValueError as exc:raise SemanticNodeError(str(exc)) from exc
        if not context.source_logical_object_id:
            raise SemanticNodeError('Source-chosen Aura protection requires its current source incarnation')
        fragment = self.protection_fragment(color, context)
        return (ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}:6',
            source_id=context.source_object_id,timestamp=context.source_timestamp,
            origin=ContinuousEffectOrigin.STATIC_ABILITY,layer=Layer.ABILITY,sublayer='6',
            relation=ContinuousEffectRelation.SOURCE_ATTACHED_TO_OBJECT,related_object=context.attached_object,
            applies=predicate,operations=(ContinuousOperation('add_ability','Protection'),
                ContinuousOperation('add_ability_fragment',ability_fragment_to_dict(fragment)))),)


    def protection_fragment(self, color, context):
        return ProtectionSpec(
            ProtectionQualityKind.COLOR, color, 3, exempt_aura_incarnation=context.source_logical_object_id,
        )


@dataclass(frozen=True, slots=True)
class SourceChosenControlledAttachmentProtectionHandler(SourceChosenAuraProtectionHandler):
    handler_id: str = CONTROLLED_ATTACHMENT_PROTECTION_HANDLER
    schema_version: int = 2
    family: str = "continuous.attached.source_chosen_color_protection"
    rule_references: tuple[str, ...] = ("109.5", "611.3a", "613.1f", "613.7e", "702.16p")
    capability_dependencies: tuple[str, ...] = (CONTROLLED_ATTACHMENT_PROTECTION_CAPABILITY,)

    def protection_fragment(self, color, context):
        try:
            retention = ProtectionAttachmentRetention(
                context.source_logical_object_id, context.attached_object.logical_object_id,
                context.source_controller, context.attachment_protection_cutoff,
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return ProtectionSpec(
            ProtectionQualityKind.COLOR, color, CONTROLLED_ATTACHMENT_PROTECTION_VERSION,
            attachment_retention=retention,
        )
