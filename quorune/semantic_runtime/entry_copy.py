from __future__ import annotations

"""Intrinsic entry-copy choices through the existing replacement journal."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from ..entry_copy import (EntryCopySpec, ENTRY_COPY_HANDLER, ENTRY_COPY_CAPABILITY,
    COPIABLE_ORACLE_FIELD, COPIABLE_FACE_FIELD, copiable_oracle_id)
from ..object_query import object_matches_query, object_query_result
from ..replacement_effects import ReplacementClass, ReplacementEffect, SetField
from ..token_copy_recipes import apply_copy_exception
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class EntryCopyHandler:
    handler_id: str = ENTRY_COPY_HANDLER
    schema_version: int = 1
    family: str = 'replacement.zone.entry_copy'
    event: str = 'zone.change'
    rule_references: tuple[str, ...] = ('614.12', '614.12a', '616.1c', '707.2', '707.5', '707.9')
    capability_dependencies: tuple[str, ...] = (ENTRY_COPY_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'copy'}, field='intrinsic entry copy')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event):
            raise SemanticNodeError('Intrinsic entry copy identity changed')
        try:
            return EntryCopySpec.from_dict(descriptor['copy'])
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self, descriptor, *, subject, component_id):
        self.validate(descriptor)
        # Candidate-dependent effects are captured by the canonical snapshot
        # coordinator, then resolved without mutable game-state reads.
        return ()


def _copiable_values(host, target, spec):
    from ..card_program_faces import selected_face_id
    record = host.card_record(target)
    if target.face_down:
        from ..continuous_effect_model import Layer
        values = dict(host._effective_card_data(target, maximum_layer=Layer.COPY))
        values['activated_abilities'] = []
        identity, face = 'custom-copy:face-down', 'front'
    else:
        values = dict(host._copyable_characteristics(target))
        identity = copiable_oracle_id(target)
        face = selected_face_id(record, target) if record is not None else 'front'
    values = apply_copy_exception(values, spec.exception)
    values[COPIABLE_ORACLE_FIELD], values[COPIABLE_FACE_FIELD] = identity, face
    return values


def _copied_entry_effects(host, subject, source, target, values):
    """Prebind copied intrinsic producers, enabled only after this copy choice."""
    from ..card_program_faces import program_matches_face
    from ..entry_counters import intrinsic_entry_counter_effects, intrinsic_entry_counters
    from ..entry_state_metrics import entry_condition_metrics
    from .zone_replacements import default_zone_change_replacement_registry
    from .self_entry_counters import dynamic_self_entry_counter_amounts_from_programs
    record = host.card_record(target)
    if record is None or target.face_down:
        return ()
    programs = tuple(p for p in host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id, active_zone='all', event='zone.change')
        if host.semantic_program_is_current_trusted(p) and program_matches_face(record, p, target))
    # This view owns no state. Cast provenance stays on the entering physical
    # object while copiable descriptors come from the selected source.
    preview = replace(source, annotations={**source.annotations, 'copy_overrides':values},
        active_face=values[COPIABLE_FACE_FIELD])
    amounts = dynamic_self_entry_counter_amounts_from_programs(host, programs=programs,
        record=record, card=preview, destination='battlefield',
        destination_controller=subject.destination_controller,
        prospective_name=values['name'], mana_colors_spent=subject.mana_colors_spent)
    copied_subject = replace(subject, object_types=tuple(sorted(set().union(
        *host._type_parts(str(values['type_line']))))), self_entry_counter_amounts=amounts,
        entry_condition_metrics=entry_condition_metrics(host, subject.destination_controller))
    effects = list(intrinsic_entry_counter_effects(object_ref=subject.object_ref,
        destination_controller=subject.destination_controller,
        counters=intrinsic_entry_counters(values, card_types=host._type_parts(str(values['type_line']))[0],
            card_subtypes=host._type_parts(str(values['type_line']))[1], keywords=values.get('keywords', ()))))
    registry = default_zone_change_replacement_registry()
    for program in programs:
        for index, descriptor in enumerate(program.handlers):
            if descriptor.get('handler_id') == ENTRY_COPY_HANDLER:
                raise SemanticNodeError('Repeated intrinsic copy-entry replacement is outside the represented composition')
            effects.extend(registry.subject_replacement_effects(descriptor, subject=copied_subject,
                component_id=f'{program.key}:{index}'))
    return tuple(replace(effect, effect_id=f'copied-entry:{target.ref}:{effect.effect_id}',
        conditions={**dict(effect.conditions), 'entry_copy_source':{'eq':target.ref}}) for effect in effects)


def entry_copy_replacement_effects(host, subject):
    """Capture each legal existing public choice as one immutable replacement."""
    if subject.destination != 'battlefield':
        return ()
    source = host.state.cards.get(subject.object_id)
    if source is None:
        return ()
    record = host.card_record(source)
    if record is None:
        return ()
    from ..card_program_faces import program_matches_face
    handler = EntryCopyHandler()
    effects = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone='all', event='zone.change'):
        if not host.semantic_program_is_current_trusted(program) or not program_matches_face(record, program, source):
            continue
        from .self_entry_counters import program_static_component_is_applicable
        if not program_static_component_is_applicable(host, program, source,
                prospective_zone='battlefield', prospective_controller=subject.destination_controller):
            continue
        for index, descriptor in enumerate(program.handlers):
            if descriptor.get('handler_id') != ENTRY_COPY_HANDLER:
                continue
            spec = handler.validate(descriptor)
            query = replace(spec.selection,
                controller=subject.destination_controller if spec.selection.controller is not None else None,
                excluded_controllers=(subject.destination_controller,) if spec.selection.excluded_controllers else ())
            for target in host._semantic_event_sources(zones={'battlefield'}):
                effective = host._effective_card_data(target)
                row = object_query_result(target, effective,
                    type_parts=host._type_parts(str(effective.get('type_line') or '')),
                    known_to_actor=True, attached_to_ref=None)
                if not object_matches_query(row, query):
                    continue
                values = _copiable_values(host, target, spec)
                effects.extend(_copied_entry_effects(host, subject, source, target, values))
                types = tuple(sorted(set().union(*host._type_parts(str(values['type_line'])))))
                effects.append(ReplacementEffect(
                    effect_id=f'{ENTRY_COPY_HANDLER}:{subject.object_ref}:{program.key}:{index}:{target.ref}',
                    source_id=subject.object_ref, event_kind='zone.change',
                    replacement_class=ReplacementClass.ENTERS_COPY,
                    conditions={'destination': {'eq':'battlefield'}, 'object_ref': {'eq':subject.object_ref},
                        'entry_copy_source': {'eq':None}},
                    operations=(SetField('entry_copy_source', target.ref), SetField('entry_copy', values),
                        SetField('object_types', types)),
                    decline_operations=(SetField('entry_copy_source', 'declined'),), optional=True,
                    label=f'{subject.object_ref}: enter as a copy of {target.ref}'))
    return tuple(effects)
