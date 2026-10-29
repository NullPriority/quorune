from __future__ import annotations

"""Captured battlefield taxes and selected-face intrinsic cost reductions."""

from dataclasses import dataclass
from ..targeted_spell_costs import TargetedSpellCostSpec,TargetedSpellCostSource,TargetedCostSubject,TARGETED_SPELL_COST_HANDLER,TARGETED_SPELL_COST_EVENT,TARGETED_SPELL_COST_CAPABILITY
from ..card_program_faces import program_matches_face
from ..entry_copy import copiable_oracle_id
from .component_registry import exact_fields
from .context import SemanticNodeError
from ..targets import TargetGroup
from ..replacement.immutable import thaw_value
from ..object_predicate import ObjectQuerySpec
from ..object_query import object_matches_query, object_query_result
from ..rules.target_cost_subjects import target_cost_subject_refs


@dataclass(frozen=True,slots=True)
class TargetedSpellCostHandler:
    handler_id: str=TARGETED_SPELL_COST_HANDLER
    schema_version: int=1
    family: str='casting.cost.targeted_fixed_generic'
    event: str=TARGETED_SPELL_COST_EVENT
    rule_references: tuple[str,...]=('601.2c','601.2f','115.1a','903.3')
    capability_dependencies: tuple[str,...]=(TARGETED_SPELL_COST_CAPABILITY,)

    def validate(self,value):
        exact_fields(value,{'handler_id','schema_version','event','modifier'},field='Target-dependent spell cost')
        if value['handler_id']!=self.handler_id or type(value['schema_version']) is not int or value['schema_version']!=1 or value['event']!=self.event:
            raise SemanticNodeError('Target-dependent spell cost identity changed')
        try:
            spec=TargetedSpellCostSpec.from_dict(value['modifier'])
            if spec.subject is TargetedCostSubject.SPELL_TARGET:TargetGroup.from_mapping(thaw_value(spec.target_schema))
            return spec
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,value,context):self.validate(value);return ()


def _self_targeted_reduction_sources(host,caster,card,spell_program):
    from ..compiled_cast_costs import _selected_face_id
    from ..targets import target_plan
    from ..target_source import spell_targeting_source
    expected=_selected_face_id(spell_program);sources=[];handler=TargetedSpellCostHandler()
    for program in host.semantics.runtime_handler_programs_for_oracle(card.oracle_id,active_zone='all',event=TARGETED_SPELL_COST_EVENT):
        if not host.semantic_program_is_current_trusted(program) or program.provenance.get('face_id')!=expected:continue
        for index,descriptor in enumerate(program.handlers):
            if descriptor.get('handler_id')!=TARGETED_SPELL_COST_HANDLER:continue
            spec=handler.validate(descriptor)
            if spec.schema_version==3:continue
            if spec.subject is not TargetedCostSubject.SPELL_TARGET:continue
            plan=target_plan(thaw_value(spec.target_schema))
            candidates=host._target_candidate_map(caster,plan,acting_source=spell_targeting_source(host,card),source_ref=card.ref)
            refs=tuple(candidates[plan.groups[0].group_id])
            sources.append(TargetedSpellCostSource(card.ref,card.logical_object_id,caster,
                f'{card.logical_object_id}:{program.key}:{index}',spec,refs))
    return sources


def targeted_spell_cost_sources(host,caster,*,card=None,spell_program=None):
    from .self_entry_counters import program_static_component_is_applicable
    sources=[];handler=TargetedSpellCostHandler()
    battlefield=tuple(sorted((card for card in host._semantic_event_sources(zones={'battlefield'})
        if not card.phased_out and card.controller in host.active_seats),key=lambda card:card.ref))
    for source in battlefield:
        record=host.card_record(source)
        if record is None:continue
        for program in host.semantics.runtime_handler_programs_for_oracle(copiable_oracle_id(source),active_zone='battlefield',event=TARGETED_SPELL_COST_EVENT):
            if (not host.semantic_program_is_current_trusted(program) or not program_matches_face(record,program,source)
                    or program.provenance.get('granted_only')
                    or not program_static_component_is_applicable(host,program,source)):continue
            for index,descriptor in enumerate(program.handlers):
                if descriptor.get('handler_id')!=TARGETED_SPELL_COST_HANDLER:continue
                spec=handler.validate(descriptor)
                if spec.schema_version in {4,5}:
                    captured=_public_target_cost_source(host,caster,source,program,index,spec,battlefield,card,spell_program)
                    if captured is not None:sources.append(captured)
                    continue
                if spec.schema_version==3:
                    if (spec.caster_relation=='source_controller' and source.controller!=caster
                            or spec.caster_relation=='source_opponents' and source.controller==caster):continue
                    if spec.subject is TargetedCostSubject.SPELL_TARGET:
                        refs=tuple(candidate.ref for candidate in battlefield
                            if 'creature' in host._type_parts(str(host._effective_card_data(candidate).get('type_line') or ''))[0])
                    else:refs=(source.ref,)
                    sources.append(TargetedSpellCostSource(source.ref,source.logical_object_id,source.controller,
                        f'{source.logical_object_id}:{program.key}:{index}',spec,refs))
                    continue
                if source.controller==caster:continue
                if spec.subject is TargetedCostSubject.SPELL_TARGET:continue
                refs=(source.ref,) if spec.subject is TargetedCostSubject.SOURCE else tuple(card.ref for card in battlefield
                    if card.controller==source.controller and card.is_commander
                    and 'creature' in host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0])
                sources.append(TargetedSpellCostSource(source.ref,source.logical_object_id,source.controller,f'{source.logical_object_id}:{program.key}:{index}',spec,refs))
    from ..rules.granted_static_costs import granted_static_cost_sources
    sources.extend(granted_static_cost_sources(host,caster,battlefield))
    if card is not None:sources.extend(_self_targeted_reduction_sources(host,caster,card,spell_program))
    return tuple(sources)


def _prospective_spell_subject(host,caster,card,program):
    from ..card_program_faces import prospective_face_view,normalized_program_face_id
    from ..compiled_cast_timing import prospective_conditional_flash_spell_data
    record=host.card_record(card)
    view=prospective_face_view(record,card,normalized_program_face_id(record,program)) if program is not None else card
    effective=prospective_conditional_flash_spell_data(host,view,caster)
    return object_query_result(view,effective,
        type_parts=host._type_parts(str(effective.get('type_line') or '')),
        known_to_actor=True,attached_to_ref=None)


def _public_target_cost_source(host,caster,source,program,index,spec,battlefield,card,spell_program):
    if (spec.caster_relation=='source_controller' and source.controller!=caster
            or spec.caster_relation=='source_opponents' and source.controller==caster):return None
    if spec.spell_predicate!=ObjectQuerySpec():
        if card is None or not object_matches_query(_prospective_spell_subject(host,caster,card,spell_program),spec.spell_predicate):
            return None
    if spec.schema_version==5:
        refs=tuple(candidate.ref for candidate in battlefield
            if 'creature' in host._type_parts(str(host._effective_card_data(candidate).get('type_line') or ''))[0]) if spec.counted_targets=='creatures' else ()
        return TargetedSpellCostSource(source.ref,source.logical_object_id,source.controller,
            f'{source.logical_object_id}:{program.key}:{index}',spec,refs)
    objects=tuple(host._public_object_query_result(candidate) for candidate in battlefield) if spec.target_subject.relation=='query' else None
    attached=host.state.cards.get(source.attached_to)
    attached_ref=attached.ref if attached is not None and attached.zone=='battlefield' and not attached.phased_out else None
    refs=target_cost_subject_refs(spec.target_subject,source_ref=source.ref,controller=source.controller,
        attached_ref=attached_ref,objects=objects)
    return TargetedSpellCostSource(source.ref,source.logical_object_id,source.controller,
        f'{source.logical_object_id}:{program.key}:{index}',spec,refs)
