from __future__ import annotations

"""Current trusted Prototype selection and immutable prospective cast views."""

from dataclasses import replace
from ..card_programs.admission import program_has_complete_card_program_admission
from ..prototype_model import PrototypeSpec,PROTOTYPE_ANNOTATION,PROTOTYPE_HANDLER
from ..semantic_runtime.prototype import PrototypeAbilityHandler


def compiled_prototype_spec(host,card):
    result=[]
    record=host.card_record(card)
    if record is None or record.layout not in {'normal','prototype'} or record.faces:return None
    for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,active_zone='all',event='characteristics.evaluate'):
        if not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program):continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id')==PROTOTYPE_HANDLER:result.append(PrototypeAbilityHandler().validate(descriptor))
    return result[0] if len(result)==1 else None


def prototype_cast_view(host,card,record,cast_method):
    if cast_method!='prototype':return card,record
    from .casting.model import CastProposalError
    spec=compiled_prototype_spec(host,card)
    if spec is None:raise CastProposalError('This card lacks a current trusted Prototype contract',reason='prototype_contract_unavailable')
    view=replace(card,annotations={**card.annotations,PROTOTYPE_ANNOTATION:spec.to_dict(),'prospective_cast_prototype':True})
    values=spec.characteristic_values()
    return view,replace(record,mana_cost=spec.mana_cost,mana_value=values['mana_value'],power=values['power'],toughness=values['toughness'],colors=tuple(values['colors']))


def prototype_characteristic_values(card):
    raw=card.annotations.get(PROTOTYPE_ANNOTATION)
    if raw is None or (card.zone not in {'stack','battlefield'} and card.annotations.get('prospective_cast_prototype') is not True):return {}
    return PrototypeSpec.from_dict(raw).characteristic_values()


def commit_prototype_cast(card,raw):
    if raw is not None:card.annotations[PROTOTYPE_ANNOTATION]=PrototypeSpec.from_dict(raw).to_dict()


def revalidate_prototype_cast(host,card,details):
    raw=details.get('prototype')
    if details.get('cast_method')!='prototype':
        if raw is not None:raise ValueError('Ordinary casting cannot carry Prototype characteristics')
        return
    proposed=PrototypeSpec.from_dict(raw)
    if proposed!=compiled_prototype_spec(host,card):raise ValueError('Prototype contract changed before commit')
