from __future__ import annotations

"""Prospective entry scopes share one source-bound replacement occurrence."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..entry_type_scopes import entry_type_scopes
from ..qualified_entry_state_model import (
    QUALIFIED_ENTRY_STATE_CAPABILITY, QUALIFIED_ENTRY_STATE_HANDLER,
    EntryStateSourceSnapshot, QualifiedEntryStateSpec,
)
from ..replacement import ReplacementClass, ReplacementEffect, SetField
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class QualifiedEntryStateHandler:
    handler_id: str = QUALIFIED_ENTRY_STATE_HANDLER
    schema_version: int = 2
    family: str = 'replacement.zone.entry_state'
    event: str = 'zone.change'
    rule_references: tuple[str, ...] = ('110.5b', '614.4', '614.5', '614.12', '616.1')
    capability_dependencies: tuple[str, ...] = (QUALIFIED_ENTRY_STATE_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> QualifiedEntryStateSpec:
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'source_relation',
            'subject_queries', 'exclude_source', 'source_tapped', 'tapped'}, field='qualified entry state')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version or descriptor['event'] != self.event):
            raise SemanticNodeError('Qualified entry state component identity changed')
        try:
            return QualifiedEntryStateSpec.from_dict({key: descriptor[key] for key in
                ('source_relation', 'subject_queries', 'exclude_source', 'source_tapped', 'tapped')})
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def source_replacement_effects(self, descriptor, *, source_ref, source_controller,
                                   component_id, source_context):
        spec = self.validate(descriptor)
        if (not isinstance(source_context, EntryStateSourceSnapshot)
                or source_context.source_ref != source_ref or source_context.controller != source_controller
                or type(component_id) is not str or not component_id):
            raise SemanticNodeError('Qualified entry state requires its current typed source snapshot')
        if spec.source_tapped is not None and source_context.tapped is not spec.source_tapped:
            return ()
        identity = f'{self.handler_id}:{source_ref}:{component_id}'
        conditions = {'destination': {'eq': 'battlefield'}}
        if spec.source_relation == 'controller':
            conditions['destination_controller'] = {'eq': source_controller}
        elif spec.source_relation == 'opponents':
            conditions['destination_controller'] = {'not_in': [source_controller]}
        if spec.exclude_source:
            conditions['object_ref'] = {'not_in': [source_ref]}
        effects = []
        for index, scope in enumerate(entry_type_scopes(spec.subject_queries)):
            types = {}
            if scope.required:
                types['contains_all'] = sorted(scope.required)
            if scope.excluded:
                types['contains_none'] = sorted(scope.excluded)
            effects.append(ReplacementEffect(effect_id=f'{identity}:{index}', source_id=source_ref,
                event_kind=self.event, replacement_class=ReplacementClass.OTHER,
                conditions={**conditions, **({'object_types': types} if types else {})},
                operations=(SetField('tapped', spec.tapped),), occurrence_id=identity,
                label=f'{source_ref}: enter {"tapped" if spec.tapped else "untapped"}'))
        return tuple(effects)
