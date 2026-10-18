from __future__ import annotations

"""Intrinsic entry-copy choices through the existing replacement journal."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from ..entry_copy import (EntryCopySpec, ENTRY_COPY_HANDLER, ENTRY_COPY_CAPABILITY,
    ENTRY_COPY_QUERY_HANDLER, ENTRY_COPY_QUERY_CAPABILITIES, ENTRY_COPY_VALUES_HANDLER, ENTRY_COPY_VALUES_CAPABILITY,
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
        expected = {ENTRY_COPY_HANDLER: 1, ENTRY_COPY_QUERY_HANDLER: 2, ENTRY_COPY_VALUES_HANDLER:3}.get(self.handler_id)
        if (self.schema_version != expected or descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version or descriptor['event'] != self.event):
            raise SemanticNodeError('Intrinsic entry copy identity changed')
        try:
            spec = EntryCopySpec.from_dict(descriptor['copy'])
            if spec.schema_version != self.schema_version:
                raise ValueError('Copy selector and handler versions differ')
            return spec
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


def entry_copy_handler(version):
    if type(version) is not int:
        raise SemanticNodeError('Copy selector version must be an integer')
    if version == 1:
        return EntryCopyHandler()
    if version == 2:
        return EntryCopyHandler(handler_id=ENTRY_COPY_QUERY_HANDLER, schema_version=2,
            capability_dependencies=ENTRY_COPY_QUERY_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references, '115.10a', '201.5'))
    if version==3:return EntryCopyHandler(handler_id=ENTRY_COPY_VALUES_HANDLER,schema_version=3,capability_dependencies=(ENTRY_COPY_CAPABILITY,ENTRY_COPY_VALUES_CAPABILITY),rule_references=(*EntryCopyHandler().rule_references,'707.9c','707.9d','205.3d'))
    raise SemanticNodeError('Copy selector version is unsupported')


def _copiable_values(host, target, spec, *, entering=None):
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
    exception=spec.exception
    if exception.retain_source_stats:
        if entering is None:raise SemanticNodeError('Retained copy stats require the entering source')
        # Printed/prototype spell values, before the copy and battlefield boosts.
        from ..carddb_characteristics import base_card_characteristics
        from ..rules.prototype import prototype_characteristic_values
        original=base_card_characteristics(entering,host.card_record(entering))
        original.update(prototype_characteristic_values(entering))
        try:power,toughness=int(original['power']),int(original['toughness'])
        except (ValueError,TypeError,KeyError) as exc:raise SemanticNodeError('Entering source stats are not represented') from exc
        exception=replace(exception,power=power,toughness=toughness,retain_source_stats=False)
    values = apply_copy_exception(values, exception)
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
            if descriptor.get('handler_id') in {ENTRY_COPY_HANDLER, ENTRY_COPY_QUERY_HANDLER, ENTRY_COPY_VALUES_HANDLER}:
                raise SemanticNodeError('Repeated intrinsic copy-entry replacement is outside the represented composition')
            from ..entry_reveal import ENTRY_REVEAL_HANDLER
            from ..devour import DEVOUR_HANDLER
            from ..preparation_model import PREPARATION_HANDLER
            if descriptor.get('handler_id')==PREPARATION_HANDLER:
                from .self_entry_counters import program_static_component_is_applicable
                if not program_static_component_is_applicable(host,program,preview,prospective_zone='battlefield',
                        prospective_controller=subject.destination_controller):continue
            if descriptor.get('handler_id')==DEVOUR_HANDLER:
                from .devour import DevourEntryHandler,devour_effects_for_spec
                effects.extend(devour_effects_for_spec(host,copied_subject,DevourEntryHandler().validate(descriptor),component_id=f'{program.key}:{index}'))
                continue
            if descriptor.get('handler_id')==ENTRY_REVEAL_HANDLER:
                from .entry_reveal import EntryRevealHandler,entry_reveal_effects_for_spec
                effects.extend(entry_reveal_effects_for_spec(host,copied_subject,
                    EntryRevealHandler().validate(descriptor),component_id=f'{program.key}:{index}'))
                continue
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
    effects = []
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id, active_zone='all', event='zone.change'):
        if not host.semantic_program_is_current_trusted(program) or not program_matches_face(record, program, source):
            continue
        from .self_entry_counters import program_static_component_is_applicable
        if not program_static_component_is_applicable(host, program, source,
                prospective_zone='battlefield', prospective_controller=subject.destination_controller):
            continue
        for index, descriptor in enumerate(program.handlers):
            if descriptor.get('handler_id') not in {ENTRY_COPY_HANDLER, ENTRY_COPY_QUERY_HANDLER, ENTRY_COPY_VALUES_HANDLER}:
                continue
            handler = entry_copy_handler(descriptor.get('schema_version'))
            spec = handler.validate(descriptor)
            query = replace(spec.selection,
                controller=subject.destination_controller if spec.selection.controller is not None else None,
                excluded_controllers=(subject.destination_controller,) if spec.selection.excluded_controllers else ())
            for target in host._semantic_event_sources(zones=set(query.zones)):
                if query.zones==('graveyard',) and not target.is_card_object:continue
                effective = host._effective_card_data(target)
                row = object_query_result(target, effective,
                    type_parts=host._type_parts(str(effective.get('type_line') or '')),
                    known_to_actor=True, attached_to_ref=None,
                    entered_this_turn=(host.state.turn_sequence > 0
                        and target.entered_battlefield_turn_sequence == host.state.turn_sequence))
                if not object_matches_query(row, query):
                    continue
                values = _copiable_values(host, target, spec, entering=source)
                effects.extend(_copied_entry_effects(host, subject, source, target, values))
                types = tuple(sorted(set().union(*host._type_parts(str(values['type_line'])))))
                effects.append(ReplacementEffect(
                    effect_id=f'{descriptor["handler_id"]}:{subject.object_ref}:{program.key}:{index}:{target.ref}',
                    source_id=subject.object_ref, event_kind='zone.change',
                    replacement_class=ReplacementClass.ENTERS_COPY,
                    conditions={'destination': {'eq':'battlefield'}, 'object_ref': {'eq':subject.object_ref},
                        'entry_copy_source': {'eq':None}},
                    operations=(SetField('entry_copy_source', target.ref), SetField('entry_copy', values),
                        SetField('object_types', types)),
                    decline_operations=(SetField('entry_copy_source', 'declined'),), optional=True,
                    label=f'{subject.object_ref}: enter as a copy of {target.ref}'))
    return tuple(effects)
