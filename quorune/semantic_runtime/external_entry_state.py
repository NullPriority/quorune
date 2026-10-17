from __future__ import annotations

"""Source-only entry replacements bound by the shared immutable zone event."""

from dataclasses import dataclass
from typing import Any,Mapping

from ..external_entry_state_model import (
    EXTERNAL_ENTRY_STATE_CAPABILITY,EXTERNAL_ENTRY_STATE_HANDLER_ID,ExternalEntryStateSpec,
)
from ..replacement import ReplacementClass,ReplacementEffect,SetField
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class ExternalEntryStateHandler:
    handler_id:str=EXTERNAL_ENTRY_STATE_HANDLER_ID
    schema_version:int=1
    family:str='replacement.zone.entry_state'
    event:str='zone.change'
    rule_references:tuple[str,...]=('207.2c','207.2d','614.1d','614.4','614.12','616.1')
    capability_dependencies:tuple[str,...]=(EXTERNAL_ENTRY_STATE_CAPABILITY,)

    def validate(self,descriptor:Mapping[str,Any])->ExternalEntryStateSpec:
        exact_fields(descriptor,{'handler_id','schema_version','event','source_relation','types_any'},field='external entry state')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=self.schema_version or descriptor['event']!=self.event):
            raise SemanticNodeError('External entry state component identity changed')
        try:return ExternalEntryStateSpec.from_dict({k:descriptor[k] for k in ('source_relation','types_any')})
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def source_replacement_effect(self,descriptor,*,source_ref,source_controller,component_id):
        spec=self.validate(descriptor)
        if any(type(value) is not str or not value for value in (source_ref,source_controller,component_id)):
            raise SemanticNodeError('External entry state requires current source and component identities')
        conditions={'destination':{'eq':'battlefield'}}
        if spec.types_any:conditions['object_types']={'contains_any':list(spec.types_any)}
        if spec.source_relation=='controller':conditions['destination_controller']={'eq':source_controller}
        elif spec.source_relation=='opponents':conditions['destination_controller']={'not_in':[source_controller]}
        return ReplacementEffect(effect_id=f'{self.handler_id}:{source_ref}:{component_id}',
            source_id=source_ref,event_kind=self.event,replacement_class=ReplacementClass.OTHER,
            conditions=conditions,operations=(SetField('tapped',True),),label=f'{source_ref}: enter tapped')
