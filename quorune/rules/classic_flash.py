from __future__ import annotations

"""Keep the chosen casting permission independent of mana and target choices."""

from dataclasses import dataclass
from typing import Mapping
from ..cast_timing import CLASSIC_FLASH_HANDLER,CLASSIC_FLASH_MARK,cast_timing_is_legal
from ..card_program_faces import program_matches_face
from ..trigger_processing import schedule_delayed_trigger


@dataclass(frozen=True,slots=True)
class ClassicFlashCastMark:
    used_intrinsic_permission: bool=True
    schema_version: int=1

    def __post_init__(self):
        if self.used_intrinsic_permission is not True or type(self.schema_version) is not int or self.schema_version!=1:
            raise ValueError('Classic Flash marker requires a validated intrinsic permission use')

    def to_dict(self):return {'schema_version':self.schema_version,'used_intrinsic_permission':True}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','used_intrinsic_permission'}:
            raise ValueError('Classic Flash marker fields are incomplete or unknown')
        return cls(value['used_intrinsic_permission'],value['schema_version'])


def has_classic_flash_permission(host,card,face_name=None):
    if card.face_down:return False
    record=host.card_record(card)
    if record is None:return False
    return any(host.semantic_program_is_current_trusted(program)
        and program_matches_face(record,program,card,prospective_name=face_name)
        and any(d.get('handler_id')==CLASSIC_FLASH_HANDLER for d in program.handlers)
        for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
            active_zone='playable',event='cast.permission'))


def _sorcery_window(state,actor):
    return actor==state.active_player and not state.stack and state.phase in {'precombat_main','postcombat_main'}


def classic_flash_proposal_details(host,request,card,face,*,permission_query):
    from .casting.model import CastProposalError
    choice=request.response().get('timing_permission')
    face_name=str(face.get('name') or '') if face else None
    if request.cast_method is not None or not has_classic_flash_permission(host,card,face_name):
        if choice is not None:raise CastProposalError('This spell has no selectable intrinsic timing permission',reason='timing_permission')
        return {}
    if choice is not None and choice not in ('normal','intrinsic'):
        raise CastProposalError('Unsupported casting permission choice',reason='timing_permission')
    if request.ignore_timing or request.during_resolution:
        if choice=='intrinsic':raise CastProposalError('This cast uses its resolution permission',reason='timing_permission')
        return {}
    if _sorcery_window(host.state,request.actor):return {}
    external=permission_query(host,request.actor,card,face=face,face_name=face_name,face_down=False,include_classic=False)
    type_line=str(face.get('type_line') or '') if face else host.card_record(card).type_line
    normal_legal=cast_timing_is_legal(host.state,request.actor,type_line,external)
    if choice=='normal' and not normal_legal:
        raise CastProposalError('An independent permission does not allow this cast now',reason='timing_permission')
    return {CLASSIC_FLASH_MARK:ClassicFlashCastMark().to_dict()} if choice=='intrinsic' or not normal_legal else {}


def classic_flash_offer_fields(host,actor,card,face,*,permission_query):
    face_name=str(face.get('name') or '') if face else None
    if not has_classic_flash_permission(host,card,face_name) or _sorcery_window(host.state,actor):return {}
    external=permission_query(host,actor,card,face=face,face_name=face_name,face_down=False,include_classic=False)
    type_line=str(face.get('type_line') or '') if face else host.card_record(card).type_line
    values=['normal','intrinsic'] if cast_timing_is_legal(host.state,actor,type_line,external) else ['intrinsic']
    return {'choice_schema':{'timing_permission':{'type':'enum','legal_values':values,'required':False,
        'default':values[0],'label':'Casting permission','option_labels':{
            'normal':'Use another timing permission','intrinsic':'Use this card (sacrifice at next cleanup)'}}}}


def commit_classic_flash_permission(host,proposal,card):
    from .action_proposals import thaw_json
    raw=thaw_json(proposal.details).get(CLASSIC_FLASH_MARK)
    if raw is None:return None
    ClassicFlashCastMark.from_dict(raw)
    if card.zone!='stack' or card.is_spell_copy or not has_classic_flash_permission(host,card,proposal.face):
        raise ValueError('Intrinsic permission use requires its current accepted physical spell')
    # The canonical cast-to-permanent transition retains this cast incarnation.
    # Countering, redirecting, recasting, or blinking advances it instead.
    effect={'op':'move_if_in_zone','card':card.ref,'from':'battlefield','destination':'graveyard',
        'transition_kind':'sacrifice','expected_zone_change_counter':card.zone_change_counter}
    return schedule_delayed_trigger(host,controller=proposal.seat,label='Sacrifice the permanent at the next cleanup',
        event_kind='step.begin',condition={'phase':'ending','step':'cleanup'},
        stack_template={'label':'Intrinsic timing — sacrifice the resulting permanent','context':{'dynamic_effects':[effect]}},
        source_object_id=card.object_id,referred_object_ids=(card.object_id,),once=True)
