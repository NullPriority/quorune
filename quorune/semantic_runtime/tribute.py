from __future__ import annotations

"""Typed Tribute entry replacement over the canonical staged entry tree."""

from dataclasses import dataclass
from ..tribute_model import TributeSpec,TRIBUTE_HANDLER,TRIBUTE_CAPABILITY
from ..replacement.tribute import tribute_entry_replacements
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class TributeEntryHandler:
    handler_id: str = TRIBUTE_HANDLER
    schema_version: int = 1
    family: str = 'replacement.zone.tribute-entry'
    event: str = 'zone.change'
    rule_references: tuple[str,...] = ('122.6','614.1c','614.12','616.1','702.104a','702.104b')
    capability_dependencies: tuple[str,...] = (TRIBUTE_CAPABILITY,)

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','event','schema_version','amount','link_id'},field='Tribute entry')
        if descriptor['handler_id']!=self.handler_id or descriptor['event']!=self.event:
            raise SemanticNodeError('Tribute entry handler identity changed')
        try:return TributeSpec.from_dict({k:v for k,v in descriptor.items() if k not in {'handler_id','event'}})
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self,descriptor,*,subject,component_id):
        spec=self.validate(descriptor)
        if subject.destination!='battlefield':return ()
        if subject.destination_controller not in subject.active_players:
            raise SemanticNodeError('Tribute requires its captured entering controller')
        return tribute_entry_replacements(spec,object_ref=subject.object_ref,
            component_id=component_id,players=subject.active_players)
