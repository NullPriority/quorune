from __future__ import annotations

"""Current hand candidates, selected identities, and canonical cost disclosure."""

from ...object_predicate import ObjectQuerySpec
from ...object_query import object_query_result,query_objects
from ...effect_runtime.hand_inspection import validate_entry_hand_reveal,begin_stack_cost_hand_reveal
from ..reveal_additional_cost import fixed_reveal_additional_cost,REVEAL_ADDITIONAL_COST_KIND
from .model import CastProposalError


def reveal_cost_selection(host,*,seat,source,cost,response,hint,choice_schema,cost_position):
    predicate=ObjectQuerySpec(zones=('hand',),owner=seat,subtypes_all=(cost.subtype,),known_to_actor=True)
    rows=[]
    for oid in host.state.players[seat].zones['hand']:
        card=host.state.cards[oid]
        if oid==source.object_id:continue
        effective=host._effective_card_data(card)
        rows.append(object_query_result(card,effective,type_parts=host._type_parts(str(effective.get('type_line') or '')),known_to_actor=True,attached_to_ref=None))
    refs=[row.ref for row in query_objects(rows,predicate)]
    if not refs:return False,None
    choice_schema['reveal_cards']={'type':'object_ref_array','legal_refs':refs,'zone':'hand','minimum':1,'maximum':1,
        'distinct':True,'payment':'reveal','label':'Reveal one matching hand card'}
    if hint:return True,None
    selected=response.get('reveal_cards')
    if type(selected) is not list or len(selected)!=1 or type(selected[0]) is not str or selected[0] not in refs:return False,None
    card=host._resolve_object(seat,selected[0],zones={'hand'},owned_only=True)
    return True,{'kind':REVEAL_ADDITIONAL_COST_KIND,'cost_position':cost_position,'selection':{
        'object_id':card.object_id,'logical_object_id':card.logical_object_id,'ref':card.ref,'player':seat,
        'oracle_id':card.oracle_id,'name':card.printed_name}}


def commit_reveal_additional_costs(host,proposal,source,item,selected_option):
    for selected in selected_option.get('selected_additional_costs',()):
        if selected.get('kind')!=REVEAL_ADDITIONAL_COST_KIND:continue
        if set(selected)!={'kind','cost_position','selection'} or type(selected['cost_position']) is not int:
            raise CastProposalError('Reveal casting selection is malformed')
        costs=selected_option.get('additional_costs',());position=selected['cost_position']
        if not 0<=position<len(costs):raise CastProposalError('Reveal casting cost position changed')
        cost=fixed_reveal_additional_cost(costs[position])
        if cost is None:raise CastProposalError('Reveal casting cost disappeared')
        card=validate_entry_hand_reveal(host,selected['selection'])
        if card.object_id==source.object_id:raise CastProposalError('A spell cannot reveal itself from hand as its cost')
        effective=host._effective_card_data(card);_,subtypes,_=host._type_parts(str(effective.get('type_line') or ''))
        if cost.subtype not in {s.casefold() for s in subtypes}:raise CastProposalError('Reveal casting subtype changed')
        begin_stack_cost_hand_reveal(host,selected['selection'],item=item,source_ref=source.ref)
