from __future__ import annotations

"""Waterbend reservations reuse the canonical mana solver and tap owner."""

from ..waterbend import WaterbendObject,WaterbendPayment,WATERBEND_EVENT
from ..object_predicate import ObjectQuerySpec
from ..object_query import object_query_result,query_objects


def waterbend_candidates(host,seat,*,excluded=()):
    rows=[]
    for oid in host.state.players[seat].zones['battlefield']:
        if oid in excluded:continue
        card=host.state.cards[oid];data=host._effective_card_data(card)
        rows.append(object_query_result(card,data,type_parts=host._type_parts(str(data.get('type_line') or '')),
            known_to_actor=True,attached_to_ref=None))
    query=ObjectQuerySpec(zones=('battlefield',),controller=seat,types_any=('artifact','creature'),tapped=False,known_to_actor=True)
    return tuple(host.state.cards[row.object_id] for row in sorted(query_objects(rows,query),key=lambda row:row.ref))


def select_waterbend_payment(host,seat,cost,requirements,response,*,hint,spend_context,excluded=()):
    candidates=waterbend_candidates(host,seat,excluded=excluded)
    maximum=min(cost.generic,int(requirements['GENERIC']))
    if hint:
        from .casting.costs import _find_improvise_plan
        reserved=tuple(host.state.cards[oid] for oid in excluded)
        found=_find_improvise_plan(host,seat,requirements,candidates,spend_context=spend_context,reserved_cards=reserved,maximum_taps=maximum)
        if found is None:return None
        remaining,selected=found
    else:
        refs=response.get('waterbend_cards',[]);byref={card.ref:card for card in candidates}
        if (type(refs) is not list or any(type(ref) is not str or ref not in byref for ref in refs)
                or len(refs)!=len(set(refs)) or len(refs)>maximum):return None
        selected=[byref[ref] for ref in refs];remaining=dict(requirements);remaining['GENERIC']-=len(selected)
        if not host._cost_is_affordable(seat,remaining,exclude_sources=set(excluded)|{card.object_id for card in selected},spend_context=spend_context):return None
    payment=WaterbendPayment(cost,tuple(WaterbendObject(card.object_id,card.ref,card.logical_object_id) for card in selected))
    schema={'waterbend_cards':{'type':'object_ref_array','legal_refs':[card.ref for card in candidates],
        'minimum':0,'maximum':maximum,'distinct':True,'required':False,'label':'Tap artifacts or creatures for Waterbend'}}
    return remaining,tuple(selected),payment,schema


def validate_waterbend_payment(host,seat,raw,*,cost,excluded=()):
    payment=WaterbendPayment.from_dict(raw)
    if payment.cost!=cost:raise ValueError('Waterbend cost changed after preparation')
    candidates={card.object_id:card for card in waterbend_candidates(host,seat,excluded=excluded)}
    selected=[]
    for obj in payment.objects:
        card=candidates.get(obj.object_id)
        if card is None or card.ref!=obj.ref or card.logical_object_id!=obj.logical_object_id:
            raise ValueError('Waterbend selected incarnation changed after preparation')
        selected.append(card)
    return tuple(selected)


def tap_waterbend_objects(host,seat,cards,*,reason):
    from ..tap_state import set_permanent_tapped,dispatch_tapped_cost_group
    for card in cards:set_permanent_tapped(host,card.ref,actor=seat,tapped=True,reason=reason,log=False,semantic_events=False)
    dispatch_tapped_cost_group(host,cards,reason=reason)


def dispatch_waterbend_occurrence(host,seat,payment,*,source_ref,reason):
    from ..trigger_processing import enqueue_trigger_batch
    batch=[]
    host._dispatch_semantic_event(WATERBEND_EVENT,{'player':seat,'controller':seat,'source':source_ref,
        'waterbend_cost':payment.cost.generic,'tapped_objects':[obj.ref for obj in payment.objects],'reason':reason},trigger_batch=batch)
    host._log(seat,'cost.waterbend',f'{seat} waterbent.',{'source':source_ref,'cost':payment.cost.generic,
        'tapped_objects':[obj.ref for obj in payment.objects]},changed_objects=[obj.object_id for obj in payment.objects],changed_players=[seat])
    enqueue_trigger_batch(host,batch)


def activation_waterbend_plan(host,seat,source,ability,requirements,response,*,hint):
    from ..mana_restrictions import ability_mana_spend_context
    result=select_waterbend_payment(host,seat,ability.waterbend,requirements,response,hint=hint,
        spend_context=ability_mana_spend_context(str(host._effective_card_data(source).get('type_line') or '')),
        excluded=(source.object_id,) if ability.tap_source else ())
    if result is not None and not hint and response.get('_waterbend_payment') not in (None,result[2].to_dict()):
        raise ValueError('Waterbend payment incarnation changed across continuation')
    return result


def pay_activation_waterbend(host,proposal,source,ability,response):
    from .action_proposals import thaw_json
    from ..mana_restrictions import ability_mana_spend_context
    raw=thaw_json(proposal.details).get('waterbend_payment')
    bind_waterbend_response(response,raw)
    excluded=(source.object_id,) if ability.tap_source else ()
    from ..errors import GameRuleError
    try:cards=validate_waterbend_payment(host,proposal.seat,raw,cost=ability.waterbend,excluded=excluded)
    except ValueError as exc:raise GameRuleError(str(exc)) from exc
    spent,activations=host._pay_for_cost(proposal.seat,thaw_json(proposal.requirements),response,
        exclude_sources=set(excluded)|{card.object_id for card in cards},
        spend_context=ability_mana_spend_context(str(host._effective_card_data(source).get('type_line') or '')))
    try:cards=validate_waterbend_payment(host,proposal.seat,raw,cost=ability.waterbend,excluded=excluded)
    except ValueError as exc:raise GameRuleError(str(exc)) from exc
    tap_waterbend_objects(host,proposal.seat,cards,reason='Waterbend activation cost')
    return [card.object_id for card in cards],activations,spent,None


def bind_waterbend_response(response,raw):
    if response.get('_waterbend_payment') not in (None,raw):raise ValueError('Waterbend payment changed across continuation')
    if not isinstance(response,dict):raise ValueError('Waterbend payment requires its canonical command payload')
    response['_waterbend_payment']=raw
