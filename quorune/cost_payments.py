from __future__ import annotations

"""Shared cost affordability and actual payment through canonical mana owners."""

from typing import Any, Mapping
from .errors import GameRuleError
from .mana import ManaPlanError, auto_plan_payment
from .util import normalize_mana_bundle, pay_mana_from_pool
from .semantic_runtime.mana_spending import current_mana_spending_policy

def _activate_payment_sources(host, seat, activations, response, spend_context):
    selections=response.get('_mana_replacement_selections')
    host._activate_mana_plan(
        seat,activations,spend_context=spend_context,
        payment_id=str(response.get('_mana_payment_id') or '') or None,
        replacement_selections_by_event=selections if isinstance(selections,Mapping) else None,
    )


def pay_for_cost(
    host, seat: str, requirements: dict[str,int], response: Mapping[str,Any], *,
    exclude_sources: set[str] | None=None, spend_context: str | None=None, snow_required: int=0,
) -> tuple[dict[str,int],list[dict[str,Any]]]:
    spending_policy = current_mana_spending_policy(host, seat, spend_context)
    activations: list[dict[str, Any]] = []
    pay_mode = response.get('pay', 'auto')
    if pay_mode == 'auto':
        sources=[source for source in host.available_mana_sources(seat,spend_context=spend_context)
                 if source.object_id not in (exclude_sources or set())]
        plan = auto_plan_payment(
            requirements,sources,
            allow_conditional=bool(response.get('allow_conditional_mana',False)) and not host.state.config.strict_mana,
            reserve=normalize_mana_bundle(response.get('reserve')),
            starting_pool=host._spendable_mana_pool(seat,spend_context),
            snow_required=snow_required,starting_snow_pool=host._snow_mana_pool(seat,spend_context),
            spending_policy=spending_policy,
        )
        activations = plan.activations
        _activate_payment_sources(host,seat,activations,response,spend_context)
        payment = plan.payment
        snow_payment = plan.snow_payment
    else:
        activations = [dict(item) for item in response.get('mana') or []]
        _activate_payment_sources(host,seat,activations,response,spend_context)
        payment = normalize_mana_bundle(response.get('payment'))
        snow_payment = normalize_mana_bundle(response.get('snow_payment'))
    if sum(snow_payment.values()) != snow_required:
        raise GameRuleError(f'Mana payment requires exactly {snow_required} snow mana')
    snow_pool = host._snow_mana_pool(seat, spend_context)
    if any((snow_payment[color] > snow_pool[color] for color in 'WUBRGC')):
        raise GameRuleError('Declared snow mana is not available')
    ordinary_pool = host._spendable_mana_pool(seat, spend_context)
    for color in 'WUBRGC':
        ordinary_pool[color] -= snow_payment[color]
    try:
        _, spent = pay_mana_from_pool(
            ordinary_pool,requirements,payment=payment,
            spending_policy=current_mana_spending_policy(host,seat,spend_context),
        )
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    combined = normalize_mana_bundle(spent)
    for color in 'WUBRGC':
        combined[color] += snow_payment[color]
    host._apply_mana_spend(seat, combined, spend_context, snow_payment=snow_payment)
    return (combined, activations)

def cost_is_affordable(
    host, seat: str, requirements: Mapping[str,int], *, exclude_sources: set[str] | None=None,
    spend_context: str | None=None, snow_required: int=0,
) -> bool:
    spending_policy = current_mana_spending_policy(host, seat, spend_context)
    try:
        sources = [source for source in host.available_mana_sources(seat, spend_context=spend_context) if source.object_id not in (exclude_sources or set())]
        auto_plan_payment(
            {key:int(requirements.get(key,0)) for key in ('GENERIC','W','U','B','R','G','C')},sources,
            starting_pool=host._spendable_mana_pool(seat,spend_context),snow_required=snow_required,
            starting_snow_pool=host._snow_mana_pool(seat,spend_context),spending_policy=spending_policy,
        )
        return True
    except ManaPlanError:
        return False
