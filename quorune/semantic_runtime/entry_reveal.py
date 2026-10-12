from __future__ import annotations

"""Read-only replacement candidates for an intrinsic hand reveal choice."""

from dataclasses import dataclass,replace
from ..entry_reveal import EntryRevealSpec,ENTRY_REVEAL_HANDLER,ENTRY_REVEAL_CAPABILITY,ENTRY_REVEAL_FIELD
from ..replacement_effects import ReplacementEffect,ReplacementClass,SetField
from ..object_query import object_query_result,object_matches_query
from ..card_program_faces import program_matches_face
from .component_registry import exact_fields
from .context import SemanticNodeError
from .self_entry_counters import program_static_component_is_applicable


@dataclass(frozen=True,slots=True)
class EntryRevealHandler:
    handler_id: str = ENTRY_REVEAL_HANDLER
    schema_version: int = 1
    family: str = 'replacement.zone.entry_reveal_hand'
    event: str = 'zone.change'
    rule_references: tuple[str,...] = ('614.12','614.12a','616.1e','701.20a','400.7')
    capability_dependencies: tuple[str,...] = (ENTRY_REVEAL_CAPABILITY,)

    def validate(self,value):
        exact_fields(value,{'handler_id','schema_version','event','spec'},field='Entry hand reveal')
        if (value['handler_id']!=self.handler_id or type(value['schema_version']) is not int
                or value['schema_version']!=1 or value['event']!=self.event):
            raise SemanticNodeError('Entry hand reveal identity changed')
        return EntryRevealSpec.from_dict(value['spec'])

    def lower(self,value,context):
        self.validate(value)
        return ()

    def subject_replacement_effects(self,value,*,subject,component_id):
        self.validate(value)
        return ()


def entry_reveal_replacement_effects(host,subject):
    if subject.destination!='battlefield' or subject.destination_controller is None:return ()
    source=host.state.cards.get(subject.object_id)
    if source is None:return ()
    record=host.card_record(source)
    if record is None:return ()
    effects=[];handler=EntryRevealHandler();actor=subject.destination_controller
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,active_zone='all',event='zone.change'):
        if (not host.semantic_program_is_current_trusted(program) or not program_matches_face(record,program,source)
                or not program_static_component_is_applicable(host,program,source,prospective_zone='battlefield',prospective_controller=actor)):
            continue
        for index,descriptor in enumerate(program.handlers):
            if descriptor.get('handler_id')!=ENTRY_REVEAL_HANDLER:continue
            spec=handler.validate(descriptor)
            effects.extend(entry_reveal_effects_for_spec(host,subject,spec,component_id=f'{program.key}:{index}'))
    return tuple(effects)


def entry_reveal_effects_for_spec(host,subject,spec,*,component_id):
    actor=subject.destination_controller
    if actor is None:return ()
    query=replace(spec.query,owner=actor)
    effects=[]
    options=[]
    for object_id in host.state.players[actor].zones['hand']:
        card=host.state.cards[object_id]
        if card.object_id==subject.object_id:continue
        data=host._effective_card_data(card)
        row=object_query_result(card,data,type_parts=host._type_parts(str(data.get('type_line') or '')),known_to_actor=True,attached_to_ref=None)
        if object_matches_query(row,query):options.append(card)
    # The decline is one independent replacement alternative. Choosing
    # a card does not set tapped false and cannot override another owner.
    for card in (*options,None):
        selected={'object_id':card.object_id,'logical_object_id':card.logical_object_id,'ref':card.ref,'player':actor,'oracle_id':card.oracle_id,'name':card.printed_name} if card is not None else 'declined'
        operations=(SetField(ENTRY_REVEAL_FIELD,selected),*((SetField('tapped',True),) if card is None else ()))
        effects.append(ReplacementEffect(effect_id=f'{ENTRY_REVEAL_HANDLER}:{subject.object_ref}:{component_id}:{card.ref if card is not None else "decline"}',
            source_id=subject.object_ref,event_kind='zone.change',replacement_class=ReplacementClass.OTHER,
            conditions={'destination':{'eq':'battlefield'},'object_ref':{'eq':subject.object_ref},ENTRY_REVEAL_FIELD:{'eq':None}},
            operations=operations,label=f'Reveal {card.printed_name} ({card.ref})' if card is not None else 'Enter tapped without revealing a card'))
    return tuple(effects)
