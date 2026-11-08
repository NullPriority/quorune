from __future__ import annotations

"""Shared cost affordability and actual payment through canonical mana owners."""

from dataclasses import replace
from typing import Any, Mapping
from .errors import GameRuleError
from .mana import ManaPlanError, auto_plan_payment, mana_modes_preserving_source
from .util import normalize_mana_bundle, pay_mana_from_pool
from .semantic_runtime.mana_spending import current_mana_spending_policy


class ExactManaReplacementReplay(dict):
    """Verify prior mana decisions are used by the retried payment."""

    def __init__(self, selections: Mapping[str, Any], payment_id: str, *, extra_event_ids=()) -> None:
        from .mana_mode_effects import mana_damage_event_belongs_to_payment

        super().__init__(selections)
        self.unused = {
            event_id for event_id in selections
            if event_id in extra_event_ids or mana_damage_event_belongs_to_payment(event_id, payment_id)
        }

    def get(self, event_id, default=None):
        self.unused.discard(event_id)
        return super().get(event_id, default)

    def validate(self) -> None:
        if self.unused:
            raise GameRuleError("The activation did not replay all of its mana replacement choices")


def sacrificing_mana_source_ids(host, seat, refs, *, spend_context, exclude_sources):
    return {
        source.ref: source.object_id
        for source in host.available_mana_sources(seat, spend_context=spend_context)
        if source.ref in refs and source.object_id not in exclude_sources
        and len(mana_modes_preserving_source(source.modes)) != len(source.modes)
    }


def _payment_mana_sources(host, seat, spend_context, exclude_sources, preserve_source_id):
    sources = []
    for source in host.available_mana_sources(seat, spend_context=spend_context):
        if source.object_id in (exclude_sources or ()):
            continue
        if source.object_id == preserve_source_id:
            modes = mana_modes_preserving_source(source.modes)
            if not modes:
                continue
            source = replace(source, modes=modes)
        sources.append(source)
    return sources


def _plan_funding(host, seat, sources, spend_context):
    if not any(mode.price for source in sources for mode in source.modes):
        return None
    from .mana_planning import ManaPlanFunding
    from .mana_provenance import PlanningManaPool

    return ManaPlanFunding(PlanningManaPool.capture(host.state.players[seat]), host._mana_restriction_allows, spend_context)


def _activate_payment_sources(host, seat, activations, response, spend_context, preserve_source_id):
    selections=response.get('_mana_replacement_selections')
    protection = {"preserve_source_id": preserve_source_id} if preserve_source_id is not None else {}
    host._activate_mana_plan(
        seat,activations,spend_context=spend_context,
        payment_id=str(response.get('_mana_payment_id') or '') or None,
        replacement_selections_by_event=selections if isinstance(selections,Mapping) else None,
        **protection,
    )


def pay_for_cost(
    host, seat: str, requirements: dict[str,int], response: Mapping[str,Any], *,
    exclude_sources: set[str] | None=None, spend_context: str | None=None, snow_required: int=0,
    preserve_source_id: str | None = None,
) -> tuple[dict[str,int],list[dict[str,Any]]]:
    spending_policy = current_mana_spending_policy(host, seat, spend_context)
    activations: list[dict[str, Any]] = []
    pay_mode = response.get('pay', 'auto')
    if pay_mode == 'auto':
        sources = _payment_mana_sources(host, seat, spend_context, exclude_sources, preserve_source_id)
        plan = auto_plan_payment(
            requirements,sources,
            allow_conditional=bool(response.get('allow_conditional_mana',False)) and not host.state.config.strict_mana,
            reserve=normalize_mana_bundle(response.get('reserve')),
            starting_pool=host._spendable_mana_pool(seat,spend_context),
            snow_required=snow_required,starting_snow_pool=host._snow_mana_pool(seat,spend_context),
            spending_policy=spending_policy,funding=_plan_funding(host,seat,sources,spend_context),
        )
        activations = plan.activations
        _activate_payment_sources(host, seat, activations, response, spend_context, preserve_source_id)
        payment = plan.payment
        snow_payment = plan.snow_payment
    else:
        activations = [dict(item) for item in response.get('mana') or []]
        _activate_payment_sources(host, seat, activations, response, spend_context, preserve_source_id)
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
    preserve_source_id: str | None = None,
) -> bool:
    spending_policy = current_mana_spending_policy(host, seat, spend_context)
    try:
        sources = _payment_mana_sources(host, seat, spend_context, exclude_sources, preserve_source_id)
        auto_plan_payment(
            {key:int(requirements.get(key,0)) for key in ('GENERIC','W','U','B','R','G','C')},sources,
            starting_pool=host._spendable_mana_pool(seat,spend_context),snow_required=snow_required,
            starting_snow_pool=host._snow_mana_pool(seat,spend_context),spending_policy=spending_policy,
            funding=_plan_funding(host,seat,sources,spend_context),
        )
        return True
    except ManaPlanError:
        return False
