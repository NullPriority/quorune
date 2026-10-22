from __future__ import annotations

"""Collect current source-granted prices without granting casting-zone access."""

from ...ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ...card_program_faces import program_matches_face
from ...card_programs.admission import program_has_complete_card_program_admission
from ...compiled_flashback import compiled_ordinary_zone_cast_permission
from ..global_alternative_cost_model import GLOBAL_ALTERNATIVE_EVENT,GLOBAL_ALTERNATIVE_HANDLER,GlobalAlternativeCostSpec
from ...object_query import object_matches_query,object_query_result
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
from ...util import stable_json
import hashlib
from collections.abc import Mapping


def is_battlefield_optional_cost_option(option,optional_id):
    identity=option.get('battlefield_alternative_cost')
    if not isinstance(identity,Mapping) or set(identity)!={'source','logical','program','grant'}:return False
    if any(type(identity[key]) is not str or not identity[key] for key in ('source','logical','program')):return False
    try:
        GlobalAlternativeCostSpec.from_dict(identity['grant'])
        expected='battlefield-alternative:'+hashlib.sha256(stable_json(identity).encode('utf-8')).hexdigest()[:24]
    except (TypeError,ValueError):return False
    return option.get('base_cost_option')==expected and option.get('id')==expected+'+'+optional_id


def battlefield_alternative_base_options(host,seat,card,*,cast_type_line,enabled,has_mana_x,x_value,
    commander_tax,temporary_permission):
    if not enabled or has_mana_x and x_value not in (None,0):return []
    if card.zone not in {'hand','command'} and not (
        compiled_ordinary_zone_cast_permission(host,seat,card) or card.zone=='exile' and temporary_permission
    ):return []
    registry=default_cast_cost_component_registry();sources=[]
    for source in host._semantic_event_sources(zones={'battlefield'}):
        if source.zone!='battlefield' or source.phased_out or source.controller!=seat:continue
        record=host.card_record(source)
        if record is None:continue
        programs=host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,active_zone='battlefield',event=GLOBAL_ALTERNATIVE_EVENT)
        for program in programs:
            if not any(d.get('handler_id')==GLOBAL_ALTERNATIVE_HANDLER for d in program.handlers):continue
            if (not host.semantic_program_is_current_trusted(program) or not program_has_complete_card_program_admission(program)
                or not program_matches_face(record,program,source)):continue
            sources.append((source,program))
    if not sources:return []
    current=host._effective_static_component_key_map()
    effective=dict(host._effective_card_data(card))
    if cast_type_line is not None:effective['type_line']=cast_type_line
    row=object_query_result(card,effective,type_parts=host._type_parts(str(effective.get('type_line') or '')),
        known_to_actor=True,attached_to_ref=None)
    result=[]
    for source,program in sources:
        if CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage or program.key not in current.get(source.object_id,()):continue
        for descriptor in program.handlers:
            if descriptor.get('handler_id')!=GLOBAL_ALTERNATIVE_HANDLER:continue
            for spec in registry.lower(descriptor,None):
                if spec.origin!='any' and card.zone!=spec.origin or not object_matches_query(row,spec.predicate):continue
                identity={'source':source.object_id,'logical':source.logical_object_id,'program':program.key,'grant':spec.to_dict()}
                identity_hash=hashlib.sha256(stable_json(identity).encode('utf-8')).hexdigest()[:24]
                requirements=dict(spec.mana);requirements['GENERIC']+=commander_tax
                result.append({'id':'battlefield-alternative:'+identity_hash,'kind':'alternate',
                    'label':'Alternative mana cost from '+source.printed_name,'requirements':requirements,
                    **({'x_value_policy':'zero'} if has_mana_x else {}),
                    'battlefield_alternative_cost':identity})
    return result
