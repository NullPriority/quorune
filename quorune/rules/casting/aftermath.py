from __future__ import annotations

"""Selected-half Aftermath permission, prohibition and compulsory stack context."""

import copy
from ...cast_lifecycles import FixedCastLifecycleKind,FIXED_CAST_LIFECYCLE_CONTEXT_FIELD,FIXED_CAST_LIFECYCLE_RUNTIME_EVENT
from ...compiled_cast_lifecycles import compiled_fixed_cast_lifecycle_spec
from ...card_program_faces import program_matches_face

AFTERMATH_CAPABILITY='casting.aftermath.selected_half'


def aftermath_spec(host,card):
    return compiled_fixed_cast_lifecycle_spec(host,card,FixedCastLifecycleKind.AFTERMATH)


def aftermath_cast_prohibition_reason(host,card):
    record=host.card_record(card)
    if record is None or record.layout!='split':return None
    programs=host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,active_zone='all',event=FIXED_CAST_LIFECYCLE_RUNTIME_EVENT)
    selected=tuple(p for p in programs if program_matches_face(record,p,card) and any(d.get('lifecycle',{}).get('kind')=='aftermath' for d in p.handlers))
    if not selected:return None
    if card.zone!='graveyard':return 'aftermath_graveyard_only'
    return None if aftermath_spec(host,card) is not None else 'aftermath_contract_unavailable'


def with_aftermath_stack_context(host,card,options):
    spec=aftermath_spec(host,card) if card.zone=='graveyard' else None
    if spec is None:return options
    result=[]
    for raw in options:
        value=copy.deepcopy(dict(raw));prior=value.get(FIXED_CAST_LIFECYCLE_CONTEXT_FIELD)
        if prior is not None and prior!=spec.to_dict():continue
        value[FIXED_CAST_LIFECYCLE_CONTEXT_FIELD]=spec.to_dict();value['fixed_cast_lifecycle_fingerprint']=spec.fingerprint;result.append(value)
    return result
