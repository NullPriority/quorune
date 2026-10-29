from __future__ import annotations

"""Current giver-owned untap rules shared by step planning and direct costs/effects."""

from ..untap_restraint_model import AttachedUntapRestraintSpec
from ..object_predicate import ObjectQuerySpec
from ..untap_step import UntapStepParticipation,UntapInstruction,UntapSubjectRelation,UntapTurnRelation


def current_attached_untap_restraints(host,card=None):
    reader=getattr(host,'_effective_ability_fragments',None)
    if reader is None:return ()
    result=[]
    for source in sorted(host.state.cards.values(),key=lambda value:value.ref):
        target=host.state.cards.get(getattr(source,'attached_to',None))
        if (source.zone!='battlefield' or source.phased_out or target is None
                or target.zone!='battlefield' or target.phased_out or card is not None and target.object_id!=card.object_id):continue
        for index,spec in enumerate(reader(source)):
            if not isinstance(spec,AttachedUntapRestraintSpec):continue
            types=host._type_parts(host._effective_card_data(target)['type_line'])[0]
            if not set(spec.types_all)<=set(types):continue
            result.append((source,target,index,spec))
    return tuple(result)


def untap_is_permitted(host,card):
    return not any(spec.mode=='all_untaps' for _source,_target,_index,spec in current_attached_untap_restraints(host,card))


def attached_restraint_step_participations(host):
    return tuple(UntapStepParticipation(
        participation_id=f'attached-untap:{source.logical_object_id}:{index}',
        source_object_id=source.object_id,source_ref=source.ref,source_controller=source.controller,
        instruction=UntapInstruction.PROHIBIT,subject_relation=UntapSubjectRelation.ATTACHED_OBJECT,
        turn_relation=UntapTurnRelation.ANY if spec.mode=='all_untaps' else UntapTurnRelation.SUBJECT_CONTROLLER,
        predicate=ObjectQuerySpec(zones=('battlefield',),types_all=spec.types_all),subject_object_id=target.object_id)
        for source,target,index,spec in current_attached_untap_restraints(host))
