from __future__ import annotations

"""Pure prepared-entry replacement in the existing zone component registry."""

from dataclasses import dataclass
from ..preparation_model import PrepareSpellSpec,PREPARATION_HANDLER,PREPARATION_CAPABILITY,PREPARE_ENTRY_FIELD
from ..replacement import ReplacementEffect,ReplacementClass
from ..replacement.operations import SetField
from .component_registry import exact_fields
from .context import SemanticNodeError


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
