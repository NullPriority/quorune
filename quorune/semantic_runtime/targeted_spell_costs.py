from __future__ import annotations

"""Read-only captured spell-tax sources in the existing cast-cost registry."""

from dataclasses import dataclass
from ..targeted_spell_costs import TargetedSpellCostSpec,TargetedSpellCostSource,TargetedCostSubject,TARGETED_SPELL_COST_HANDLER,TARGETED_SPELL_COST_EVENT,TARGETED_SPELL_COST_CAPABILITY
from ..card_program_faces import program_matches_face
from ..entry_copy import copiable_oracle_id
from .component_registry import exact_fields
from .context import SemanticNodeError


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
        try:return TargetedSpellCostSpec.from_dict(value['modifier'])
        except (TypeError,ValueError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self,value,context):self.validate(value);return ()


def targeted_spell_cost_sources(host,caster):
    from .self_entry_counters import program_static_component_is_applicable
    sources=[];handler=TargetedSpellCostHandler()
    battlefield=tuple(sorted((card for card in host._semantic_event_sources(zones={'battlefield'})
        if not card.phased_out and card.controller in host.active_seats),key=lambda card:card.ref))
    for source in battlefield:
        if source.controller==caster:continue
        record=host.card_record(source)
        if record is None:continue
        for program in host.semantics.runtime_handler_programs_for_oracle(copiable_oracle_id(source),active_zone='battlefield',event=TARGETED_SPELL_COST_EVENT):
            if (not host.semantic_program_is_current_trusted(program) or not program_matches_face(record,program,source)
                    or not program_static_component_is_applicable(host,program,source)):continue
            for index,descriptor in enumerate(program.handlers):
                if descriptor.get('handler_id')!=TARGETED_SPELL_COST_HANDLER:continue
                spec=handler.validate(descriptor)
                refs=(source.ref,) if spec.subject is TargetedCostSubject.SOURCE else tuple(card.ref for card in battlefield
                    if card.controller==source.controller and card.is_commander
                    and 'creature' in host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0])
                sources.append(TargetedSpellCostSource(source.ref,source.logical_object_id,source.controller,f'{source.logical_object_id}:{program.key}:{index}',spec,refs))
    return tuple(sources)
