"""Chosen source-only qualified graveyard permission; no mutation authority."""
from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..death_cast_permission_model import (DeathCastPermissionSpec,DEATH_CAST_PERMISSION_HANDLER,
    DEATH_CAST_PERMISSION_CAPABILITY)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class QualifiedDeathCastPermissionHandler:
    handler_id: str=DEATH_CAST_PERMISSION_HANDLER
    schema_version: int=1
    family: str='permission.cast.self-zone'
    event: str='cast.zone.permission'
    rule_references: tuple[str,...]=('113.6','400.7h','601.2','614.1c','122.6')
    capability_dependencies: tuple[str,...]=(DEATH_CAST_PERMISSION_CAPABILITY,
        'condition.history.fixed_public_query','zone.turn_history.permanent_death',
        'condition.history.qualified_death_exclusion')

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','source_zone','permission',
            REQUIRES_COMPLETE_CARD_PROGRAM_FIELD},field='Qualified source death permission')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=1 or descriptor['event']!=self.event
                or descriptor['source_zone']!='graveyard' or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Qualified source death permission identity or complete-card admission changed')
        try:spec=DeathCastPermissionSpec.from_dict(descriptor['permission'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc
        if spec.entry_counters!=1:raise SemanticNodeError('This chosen permission requires its correlated one-counter entry rider')
        return spec

    def lower(self,descriptor,context):
        return (self.validate(descriptor),)
