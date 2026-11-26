from __future__ import annotations

"""Current hand candidates, selected identities, and canonical cost disclosure."""

from ...object_predicate import ObjectQuerySpec
from ...object_query import exact_numeric_characteristic, object_query_result, query_objects
from ...effect_runtime.hand_inspection import validate_entry_hand_reveal,begin_stack_cost_hand_reveal
from ...public_quantity_reductions import public_mana_value
from ...scalar_effect_amount_model import PAID_REVEALED_CREATURE_CONTEXT, PaidRevealedCreatureSnapshot
from ..reveal_additional_cost import fixed_reveal_additional_cost,REVEAL_ADDITIONAL_COST_KIND
from .model import CastProposalError


def reveal_cost_selection(host,*,seat,source,cost,response,hint,choice_schema,cost_position):
    qualifier = {"types_all": ("creature",)} if cost.reveals_creature_type else {"subtypes_all": (cost.subtype,)}
    predicate = ObjectQuerySpec(zones=("hand",), owner=seat, known_to_actor=True, **qualifier)
    rows=[]
    for oid in host.state.players[seat].zones['hand']:
        card=host.state.cards[oid]
        if oid==source.object_id:continue
        effective=host._effective_card_data(card)
        rows.append(object_query_result(card,effective,type_parts=host._type_parts(str(effective.get('type_line') or '')),known_to_actor=True,attached_to_ref=None))
    refs=[row.ref for row in query_objects(rows,predicate)]
    if not refs:return False,None
    label = "Reveal a creature card" if cost.reveals_creature_type else "Reveal one matching hand card"
    choice_schema['reveal_cards']={'type':'object_ref_array','legal_refs':refs,'zone':'hand','minimum':1,'maximum':1,
        'distinct':True,'payment':'reveal','label':label}
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
        effective = host._effective_card_data(card)
        types, subtypes, _ = host._type_parts(str(effective.get("type_line") or ""))
        eligible = "creature" in types if cost.reveals_creature_type else cost.subtype in {s.casefold() for s in subtypes}
        if not eligible:
            raise CastProposalError("Reveal casting creature type changed" if cost.reveals_creature_type
                                    else "Reveal casting subtype changed")
        if cost.reveals_creature_type:
            _bind_paid_creature_reveal(item, card, effective)
        begin_stack_cost_hand_reveal(host,selected['selection'],item=item,source_ref=source.ref)


def _bind_paid_creature_reveal(item, card, effective):
    if PAID_REVEALED_CREATURE_CONTEXT in item.context:
        raise CastProposalError("The creature reveal receipt requires one selected payment")
    try:
        snapshot = PaidRevealedCreatureSnapshot(
            stack_ref=item.ref, logical_object_id=card.logical_object_id,
            mana_value=public_mana_value(effective), power=exact_numeric_characteristic(card, effective, "power"),
        )
    except ValueError as exc:
        raise CastProposalError(str(exc), reason="paid_creature_reveal_invalid") from exc
    item.context[PAID_REVEALED_CREATURE_CONTEXT] = snapshot.to_dict()
