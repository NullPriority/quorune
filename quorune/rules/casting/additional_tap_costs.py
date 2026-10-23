from __future__ import annotations

"""Identity-pinned fixed tap reservations composing existing mana/payment owners."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from ...object_predicate import ObjectQuerySpec
from ...object_query import object_query_result,query_objects
from ..activation_costs import FixedTapCostCandidate
from ..tap_additional_cost import fixed_tap_additional_cost,FixedTapAdditionalCost


def _tap_cost(option):
    costs=option.get('additional_costs',())
    found=[cost for raw in costs if (cost:=fixed_tap_additional_cost(raw)) is not None]
    if found and (len(found)!=1 or len(costs)!=1):raise ValueError('Fixed tap casting price requires its single closed descriptor')
    return found[0] if found else None


def _candidates(host,seat,cost):
    rows=[]
    for oid in host.state.players[seat].zones['battlefield']:
        card=host.state.cards[oid];data=host._effective_card_data(card)
        rows.append(object_query_result(card,data,type_parts=host._type_parts(str(data.get('type_line') or '')),
            known_to_actor=True,attached_to_ref=None))
    predicate=ObjectQuerySpec(zones=('battlefield',),controller=seat,types_any=cost.permanent_types,
        subtypes_all=cost.creature_subtypes,tapped=False,known_to_actor=True)
    return tuple(host.state.cards[row.object_id] for row in sorted(query_objects(rows,predicate),key=lambda r:r.ref))


def _fact(cost,cards):
    return {'schema_version':1,'cost':cost.to_descriptor(),'objects':[asdict(FixedTapCostCandidate(c.object_id,c.ref,c.logical_object_id)) for c in cards]}


def validate_fixed_tap_payment(host,seat,source,option):
    cost=_tap_cost(option);raw=option.get('fixed_additional_tap_payment')
    if cost is None:
        if raw is not None:raise ValueError('Fixed tap payment has no current casting price')
        return ()
    if (not isinstance(raw,Mapping) or set(raw)!={'schema_version','cost','objects'}
            or type(raw['schema_version']) is not int or raw['schema_version']!=1
            or FixedTapAdditionalCost.from_descriptor(raw['cost'])!=cost or type(raw['objects']) is not list
            or len(raw['objects'])!=cost.count):raise ValueError('Fixed tap payment snapshot is malformed')
    identities=[]
    for row in raw['objects']:
        if not isinstance(row,Mapping) or set(row)!={'object_id','object_ref','logical_object_id'}:raise ValueError('Fixed tap object identity is malformed')
        identities.append(FixedTapCostCandidate(**row))
    if (len({v.object_id for v in identities})!=cost.count or len({v.object_ref for v in identities})!=cost.count
            or len({v.logical_object_id for v in identities})!=cost.count):raise ValueError('Fixed tap payment repeats an object')
    current={c.object_id:c for c in _candidates(host,seat,cost)};result=[]
    for identity in identities:
        card=current.get(identity.object_id)
        if (card is None or card.object_id==source.object_id or card.ref!=identity.object_ref
                or card.logical_object_id!=identity.logical_object_id):raise ValueError('Fixed tap cost source changed since its proposal')
        result.append(card)
    return tuple(result)


def _partial_payment_possible(host,seat,option,mechanics,response,*,spend_context,selected):
    from .costs import _apply_payment_mechanics
    candidate=deepcopy(option)
    payment=_apply_payment_mechanics(host,seat,candidate,mechanics,response,hint=True,spend_context=spend_context,reserved_cards=selected)
    if payment is None:return False
    # The ordinary owners jointly account for Convoke, Improvise, Delve and mana.
    return host._cost_is_affordable(seat,candidate['requirements'],exclude_sources={c.object_id for c in payment[1]},spend_context=spend_context)


def _payable_selections(host,seat,option,cost,candidates,mechanics,response,spend_context):
    """Exact search, pruning when even fewer reservations cannot leave a payment."""
    def search(start,selected):
        if len(candidates)-start<cost.count-len(selected):return
        if not _partial_payment_possible(host,seat,option,mechanics,response,spend_context=spend_context,selected=selected):return
        if len(selected)==cost.count:
            yield tuple(selected)
            return
        for index in range(start,len(candidates)):
            yield from search(index+1,(*selected,candidates[index]))
    yield from search(0,())


def finalize_fixed_tap_option(host,seat,source,option,program,mandatory,mechanics,response,*,hint,spend_context,has_x,cast_type_line):
    from .costs import _finalize_option_payment
    probe={**option,'additional_costs':list(mandatory)}
    try:cost=_tap_cost(probe)
    except ValueError:return None
    common=dict(hint=hint,spend_context=spend_context,has_x=has_x,cast_type_line=cast_type_line)
    if cost is None:return _finalize_option_payment(host,seat,source,option,program,mandatory,mechanics,response,**common)
    candidates=_candidates(host,seat,cost)
    if len(candidates)<cost.count:return None
    refs=[c.ref for c in candidates]
    choice={'tap_cost_cards':{'type':'object_ref_array','legal_refs':refs,'minimum':cost.count,'maximum':cost.count,
        'distinct':True,'zone':'battlefield','payment':'tap','label':'Tap matching untapped permanents'}}
    if hint:
        selections=_payable_selections(host,seat,option,cost,candidates,mechanics,response,spend_context)
    else:
        values=response.get('tap_cost_cards');byref={c.ref:c for c in candidates}
        if (type(values) is not list or len(values)!=cost.count or any(type(v) is not str for v in values)
                or len(set(values))!=len(values) or any(v not in byref for v in values)):return None
        selections=(tuple(byref[v] for v in values),)
    for selected in selections:
        candidate=deepcopy(option);candidate['additional_costs']=list(mandatory)
        candidate['fixed_additional_tap_payment']=_fact(cost,selected)
        if hint:candidate.setdefault('recommended_payment_refs',{})['tap_cost_cards']=[c.ref for c in selected]
        result=_finalize_option_payment(host,seat,source,candidate,program,mandatory,mechanics,response,
            reserved_cards=selected,tap_choice_schema=choice,**common)
        if result is not None:return result
    return None
