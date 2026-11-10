from __future__ import annotations

"""Pure prepared-entry replacement and source-result lowering."""

from dataclasses import dataclass
from ..preparation_model import (
    PrepareSpellSpec, PrepareSourceIntent, PREPARATION_HANDLER, PREPARATION_CAPABILITY,
    PREPARE_ENTRY_FIELD, PREPARATION_RESULT_OPERATION, PREPARATION_RESULT_CAPABILITY,
    PREPARATION_RESULT_RULES, preparation_result_spell,
)
from ..replacement import ReplacementEffect,ReplacementClass
from ..replacement.operations import SetField
from .component_registry import exact_fields
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class PrepareSourceHandler:
    operation: str = PREPARATION_RESULT_OPERATION
    handler_id: str = "permanent.preparation.prepare_source.v1"
    schema_version: int = 1
    family: str = "permanent.preparation"
    rule_references: tuple[str, ...] = PREPARATION_RESULT_RULES
    capability_dependencies: tuple[str, ...] = (PREPARATION_RESULT_CAPABILITY,)

    def lower(self, effect, context: ReadOnlyHandlerContext) -> IntentPlan:
        try:
            preparation_result_spell(effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        source = context.source
        if source is None:
            raise SemanticNodeError("Source preparation requires a stack source")
        intent = PrepareSourceIntent(source.object_id, source.logical_object_id)
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))


@dataclass(frozen=True,slots=True)
class PreparationEntryHandler:
    handler_id: str=PREPARATION_HANDLER
    schema_version: int=1
    family: str='replacement.zone.prepared_entry'
    event: str='zone.change'
    rule_references: tuple[str,...]=('722.3a','722.3c','614.12','601.2i')
    capability_dependencies: tuple[str,...]=(PREPARATION_CAPABILITY,)

    def validate(self,value):
        exact_fields(value,{'handler_id','schema_version','event','spell','requires_complete_card_program'},field='Prepared entry')
        if (value['handler_id']!=self.handler_id or type(value['schema_version']) is not int or value['schema_version']!=1
                or value['event']!=self.event or value['requires_complete_card_program'] is not True):
            raise SemanticNodeError('Prepared entry requires its exact identity and complete-source boundary')
        try:return PrepareSpellSpec.from_dict(value['spell'])
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,value,context):self.validate(value);return ()

    def subject_replacement_effects(self,value,*,subject,component_id):
        spell=self.validate(value)
        if subject.origin=='battlefield' or subject.destination!='battlefield':return ()
        return (ReplacementEffect(effect_id=f'{self.handler_id}:{subject.object_ref}:{component_id}',source_id=subject.object_ref,
            event_kind='zone.change',replacement_class=ReplacementClass.OTHER,
            conditions={'object_ref':{'eq':subject.object_ref},'destination':{'eq':'battlefield'}},
            operations=(SetField(PREPARE_ENTRY_FIELD,spell.to_dict()),)),)
