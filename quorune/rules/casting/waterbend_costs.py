from __future__ import annotations

"""One mandatory Waterbend price around the existing casting payment stages."""

from copy import deepcopy
from ...waterbend import fixed_waterbend_cost
from ..waterbend_payments import select_waterbend_payment,validate_waterbend_payment


def waterbend_cast_cost(costs):
    found=[cost for raw in costs if (cost:=fixed_waterbend_cost(raw)) is not None]
    if found and (len(found)!=1 or len(costs)!=1):raise ValueError('Waterbend requires one complete mandatory casting cost')
    return found[0] if found else None


def add_waterbend_requirements(option,costs):
    cost=waterbend_cast_cost(costs)
    if cost is not None:option['requirements']['GENERIC']+=cost.generic


def finalize_waterbend_cast_option(host,seat,card,option,program,costs,mechanics,response,*,hint,spend_context,has_x,cast_type_line):
    from .additional_tap_costs import finalize_fixed_tap_option
    from .costs import _finalize_option_payment
    cost=waterbend_cast_cost(costs)
    args=dict(hint=hint,spend_context=spend_context,has_x=has_x,cast_type_line=cast_type_line)
    if cost is None:return finalize_fixed_tap_option(host,seat,card,option,program,costs,mechanics,response,**args)
    if has_x or mechanics:return None
    payment=select_waterbend_payment(host,seat,cost,option['requirements'],response,hint=hint,spend_context=spend_context)
    if payment is None:return None
    remaining,cards,plan,schema=payment;candidate=deepcopy(option)
    if not hint and response.get('_waterbend_payment') not in (None,plan.to_dict()):return None
    candidate['requirements']=remaining;candidate['waterbend_payment']=plan.to_dict();candidate['additional_costs']=list(costs)
    if hint:candidate.setdefault('recommended_payment_refs',{})['waterbend_cards']=[obj.ref for obj in plan.objects]
    return _finalize_option_payment(host,seat,card,candidate,program,costs,mechanics,response,
        reserved_cards=cards,tap_choice_schema=schema,**args)


def validate_cast_waterbend(host,seat,source,option):
    cost=waterbend_cast_cost(option.get('additional_costs',()))
    if cost is None:
        if 'waterbend_payment' in option:raise ValueError('Waterbend payment has no current price')
        return ()
    return validate_waterbend_payment(host,seat,option.get('waterbend_payment'),cost=cost)
