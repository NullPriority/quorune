from __future__ import annotations

"""Compiled Devour producers and immutable public sacrifice candidates."""

from dataclasses import dataclass,replace
from ..devour import DevourSpec,DevourQuality,DevourObject,DEVOUR_HANDLER,DEVOUR_CAPABILITY
from ..object_predicate import ObjectQuerySpec
from ..object_query import object_query_result,query_objects
from ..replacement import ReplacementEffect,ReplacementClass,DevourEntryOperation
from ..card_program_faces import program_matches_face,prospective_face_view
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True,slots=True)
class DevourEntryHandler:
    handler_id: str=DEVOUR_HANDLER
    schema_version: int=1
    family: str='replacement.zone.devour'
    event: str='zone.change'
    rule_references: tuple[str,...]=('702.82a','702.82c','614.12','616.1g','701.21a','122.6')
    capability_dependencies: tuple[str,...]=(DEVOUR_CAPABILITY,'counter.placement.quantity_replacement')

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','devour'},field='Devour entry component')
        if descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version']!=1 or descriptor['event']!=self.event:
            raise SemanticNodeError('Devour entry component identity changed')
        try:return DevourSpec.from_dict(descriptor['devour'])
        except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,descriptor,context):self.validate(descriptor);return ()
    def subject_replacement_effects(self,descriptor,*,subject,component_id):self.validate(descriptor);return ()


def devour_effects_for_spec(host,subject,spec,*,component_id):
    if subject.destination!='battlefield' or subject.origin=='battlefield':return ()
    controller=subject.destination_controller
    if controller is None:raise SemanticNodeError('Devour entry needs its destination controller')
    query=ObjectQuerySpec(zones=('battlefield',),controller=controller,
        types_all=('creature',) if spec.quality is DevourQuality.CREATURE else ('artifact',),
        subtypes_all=('food',) if spec.quality is DevourQuality.FOOD else (),known_to_actor=True)
    rows=[]
    for card in host._semantic_event_sources(zones={'battlefield'}):
        data=host._effective_card_data(card)
        rows.append(object_query_result(card,data,type_parts=host._type_parts(str(data.get('type_line') or '')),
            known_to_actor=True,attached_to_ref=None))
    candidates=tuple(DevourObject(row.object_id,row.logical_object_id,row.ref)
        for row in sorted(query_objects(rows,query),key=lambda row:(row.ref,row.object_id,row.logical_object_id)))
    operation=DevourEntryOperation(controller,component_id,spec,candidates)
    return (ReplacementEffect(effect_id=f'{DEVOUR_HANDLER}:{subject.object_ref}:{component_id}',source_id=subject.object_ref,event_kind='zone.change',
        replacement_class=ReplacementClass.OTHER,conditions={'destination':{'eq':'battlefield'},'object_ref':{'eq':subject.object_ref}},
        operations=(operation,),optional=True,label=f'{subject.object_ref}: Devour'),)


def devour_replacement_effects(host,subject,*,prospective_objects=None):
    if subject.destination!='battlefield' or subject.origin=='battlefield':return ()
    from .self_entry_counters import program_static_component_is_applicable
    card=(prospective_objects or {}).get(subject.object_id) or host.state.cards.get(subject.object_id)
    if card is None:return ()
    record=host.card_record(card)
    if record is None:return ()
    effects=[];handler=DevourEntryHandler()
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,active_zone='all',event='zone.change'):
        if (not host.semantic_program_is_current_trusted(program) or not program_matches_face(record,program,card,
                prospective_name=subject.entry_face_id if subject.entry_face_id!='front' else None)
                or not program_static_component_is_applicable(host,program,prospective_face_view(record,card,subject.entry_face_id),
                    prospective_zone='battlefield',prospective_controller=subject.destination_controller)):continue
        for index,descriptor in enumerate(program.handlers):
            if descriptor.get('handler_id')==DEVOUR_HANDLER:
                effects.extend(devour_effects_for_spec(host,subject,handler.validate(descriptor),component_id=f'{program.key}:{index}'))
    return tuple(effects)


def capture_devour_candidates(host,snapshot,*,error_type):
    ids=tuple(dict.fromkeys(c.object_id for effect in snapshot.effects for op in effect.operations
        if isinstance(op,DevourEntryOperation) for c in op.candidates))
    if not ids:return snapshot
    from .zone_replacements import capture_zone_change_replacement_snapshot
    from ..zone_trigger_events import ZoneTransitionKind
    captured=capture_zone_change_replacement_snapshot(host,tuple((oid,'graveyard') for oid in ids),
        transition_kinds={oid:ZoneTransitionKind.SACRIFICE for oid in ids},error_type=error_type)
    effects={effect.effect_id:effect for effect in snapshot.effects}
    for effect in captured.effects:
        if effect.effect_id in effects and effects[effect.effect_id]!=effect:raise error_type('Devour capture found conflicting replacement definitions')
        effects[effect.effect_id]=effect
    return replace(snapshot,devour_candidates=captured.subjects,effects=tuple(effects.values()))
