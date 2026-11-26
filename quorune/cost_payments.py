from __future__ import annotations

"""Shared cost affordability and actual payment through canonical mana owners."""

from dataclasses import dataclass, replace
from typing import Any, Mapping
from .errors import GameRuleError
from .mana import ManaPlanError, auto_plan_payment, mana_modes_preserving_source
from .rules.mana_pool_payment import MANA_POOL_PAYMENT_FIELD, ManaPoolAllocation, decode_pool_payment
from .util import normalize_mana_bundle, pay_mana_from_pool
from .semantic_runtime.mana_spending import current_mana_spending_policy


def available_payment_mana_steps(host, seat, spend_context, excluded=(), *, include_unspendable=False):
    """Quote explicit, currently selectable mana steps for one payer."""
    from .artifact_hand_mana_model import (
        ARTIFACT_HAND_MANA_REVEAL_FIELD, ARTIFACT_HAND_MANA_STEP, ArtifactHandManaStep,
    )
    from .mana import ManaStepInput
    from .mana_planning import available_mode_payments
    from .rules.hand_artifact_mana import artifact_hand_mana_choice
    from .triggered_mana import triggered_mana_mode_label, triggered_mana_selection
    from .triggered_mana_model import TRIGGERED_MANA_OPTION_FIELD

    if include_unspendable:
        from .mana_source_discovery import available_mana_sources

        sources = available_mana_sources(host, seat, spend_context=spend_context, include_unspendable=True)
    else:
        sources = host.available_mana_sources(seat, spend_context=spend_context)
    modes = {}
    for source in sources:
        if source.object_id in excluded:
            continue
        for mode in source.modes:
            bundle = {color: count for color, count in mode.bundle.items() if count}
            activation = {"source": source.ref, "tap_source": mode.tap_source, "bundle": bundle}
            if include_unspendable and mode.restriction:
                activation["restriction"] = mode.restriction
            if mode.artifact_hand_mana is not None:
                artifact = ArtifactHandManaStep(mode.artifact_hand_mana, ())
                activation.update({"bundle": {}, ARTIFACT_HAND_MANA_STEP: artifact.to_dict()})
                schema = {ARTIFACT_HAND_MANA_REVEAL_FIELD: artifact_hand_mana_choice(host, seat)}
                key = (source.ref, ARTIFACT_HAND_MANA_STEP, mode.artifact_hand_mana)
                modes[key] = activation, source.name, schema
                continue
            if mode.requires_choice or (mode.conditional and not mode.intermediate_only and not (
                mode.restriction and host._mana_restriction_allows(mode.restriction, spend_context)
            )):
                continue
            for payment in available_mode_payments(mode, host.state.players[seat], host._mana_restriction_allows):
                proposed = dict(activation)
                step_input = ManaStepInput(*mode.price.key, payment) if mode.price else None
                if step_input is not None:
                    proposed["input"] = step_input.to_dict()
                selection = triggered_mana_selection(mode.bundle, mode.triggered_mana)
                if selection is not None:
                    proposed[TRIGGERED_MANA_OPTION_FIELD] = selection
                input_key = (*step_input.key, tuple(sorted(step_input.payment.items()))) if step_input else None
                identity = (
                    source.ref, tuple(sorted(bundle.items())), mode.tap_source, input_key,
                    selection, activation.get("restriction"),
                )
                label = f"{source.name} ({triggered_mana_mode_label(mode)})" if selection is not None else source.name
                modes.setdefault(identity, (proposed, label, None))
    return tuple(modes.values())


@dataclass(frozen=True, slots=True)
class ManaCostPayment:
    allocation: ManaPoolAllocation
    activations: list[dict[str, Any]]

    @property
    def spent(self) -> dict[str, int]:
        return self.allocation.bundles()[0]


class ExactManaReplacementReplay(dict):
    """Verify prior mana decisions are used by the retried payment."""

    def __init__(self, selections: Mapping[str, Any], payment_id: str, *, extra_event_ids=()) -> None:
        from .mana_mode_effects import mana_result_event_belongs_to_payment

        super().__init__(selections)
        self.unused = {
            event_id for event_id in selections
            if event_id in extra_event_ids or mana_result_event_belongs_to_payment(event_id, payment_id)
        }

    def get(self, event_id, default=None):
        self.unused.discard(event_id)
        return super().get(event_id, default)

    def validate(self) -> None:
        if self.unused:
            raise GameRuleError("The activation did not replay all of its mana replacement choices")


def mana_step_replacement_event_id(required, seat, source, payment_id):
    """Bind a suspended mana step to its original cost or result event."""
    from .mana_mode_effects import mana_result_event_belongs_to_payment, mana_sacrifice_payment_event_id
    from .replacement_decisions import _mana_damage_event_id
    from .rules.mana_life_result import mana_life_replacement_event
    from .rules.mana_point_result import mana_point_replacement_event

    response = {"_mana_payment_id": payment_id}
    life = mana_life_replacement_event(required.batch, response, seat)
    if life is not None:
        if life.payload.get("source") != source:
            raise GameRuleError("Mana life replacement changed its original source")
        return life.event_id
    point = mana_point_replacement_event(required.batch, response, seat)
    if point is not None:
        if point.payload.get("source") != source:
            raise GameRuleError("Mana point placement changed its original source")
        return point.event_id
    events = required.batch.events
    if len(events) == 1 and events[0].kind == "zone.change":
        event = events[0]
        if (event.payload.get("origin") != "battlefield" or event.payload.get("object_ref") != source
                or event.affected_object is None or event.affected_object.controller != seat):
            raise GameRuleError("Mana sacrifice replacement changed its original payer or source")
        return mana_sacrifice_payment_event_id(payment_id, source)
    event_id = _mana_damage_event_id(required.batch)
    if not mana_result_event_belongs_to_payment(event_id, payment_id):
        raise GameRuleError("Mana damage replacement belongs to another activation")
    return event_id


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
    if not any(mode.price or mode.triggered_mana for source in sources for mode in source.modes):
        return None
    from .mana_planning import ManaPlanFunding
    from .mana_provenance import PlanningManaPool

    return ManaPlanFunding(PlanningManaPool.capture(host.state.players[seat]), host._mana_restriction_allows, spend_context)


def _activate_payment_sources(host, seat, activations, response, spend_context, preserve_source_id, *,
                              exclude_sources=None, hand_mana_requested=True):
    if activations and (exclude_sources or not hand_mana_requested):
        unavailable = {
            host.state.cards[oid].ref
            for oid in host.state.players[seat].zones["hand"]
            if not hand_mana_requested or oid in exclude_sources
        }
        if any(row.get("source") in unavailable for row in activations):
            raise GameRuleError("A reserved hand card cannot be spent as a mana source")
    selections=response.get('_mana_replacement_selections')
    protection = {"preserve_source_id": preserve_source_id} if preserve_source_id is not None else {}
    host._activate_mana_plan(
        seat,activations,spend_context=spend_context,
        payment_id=str(response.get('_mana_payment_id') or '') or None,
        replacement_selections_by_event=selections if isinstance(selections,Mapping) else None,
        **protection,
    )


def _pay_from_selected_pool(
    host, seat, requirements, response, *, spend_context, snow_required, preserve_source_id, exclude_sources,
) -> ManaCostPayment:
    if any(key in response for key in ("payment", "snow_payment", "reserve")):
        raise GameRuleError("An exact mana pool payment replaces the color payment and reserve fields")
    activations = [dict(item) for item in response.get("mana") or []]
    _activate_payment_sources(host, seat, activations, response, spend_context, preserve_source_id,
                              exclude_sources=exclude_sources,
                              hand_mana_requested=bool(any(requirements.values()) or snow_required))
    player = host.state.players[seat]
    try:
        allocation = decode_pool_payment(response[MANA_POOL_PAYMENT_FIELD], player)
        allocation.require_available(player, host._mana_restriction_allows, spend_context)
        total, snow = allocation.bundles()
        plan = auto_plan_payment(
            requirements, (), starting_pool=total, starting_snow_pool=snow, snow_required=snow_required,
            spending_policy=current_mana_spending_policy(host, seat, spend_context),
        )
        combined = {color: plan.payment.get(color, 0) + plan.snow_payment.get(color, 0) for color in "WUBRGC"}
        if combined != total:
            raise GameRuleError("Select exactly the mana needed to pay the cost")
        paid = host._apply_mana_spend(
            seat, combined, spend_context, snow_payment=plan.snow_payment, pool_allocation=allocation,
        )
    except (ValueError, ManaPlanError) as exc:
        raise GameRuleError(str(exc)) from exc
    return ManaCostPayment(paid, activations)


def pay_for_cost(
    host, seat: str, requirements: dict[str,int], response: Mapping[str,Any], *,
    exclude_sources: set[str] | None=None, spend_context: str | None=None, snow_required: int=0,
    preserve_source_id: str | None = None,
) -> ManaCostPayment:
    from .artifact_hand_mana_model import ARTIFACT_HAND_MANA_STEP
    from .rules.hand_artifact_mana import artifact_hand_mana_payment_steps, validate_artifact_payment_sources

    submitted = response.get("mana") or ()
    fields = artifact_hand_mana_payment_steps(response)
    has_artifact_steps = isinstance(submitted, (list, tuple)) and any(
        isinstance(row, Mapping) and ARTIFACT_HAND_MANA_STEP in row for row in submitted
    )
    if fields:
        if submitted or response.get("pay", "auto") != "auto" or MANA_POOL_PAYMENT_FIELD in response:
            raise GameRuleError("Use one declaration for artifact mana payment")
        response = {**response, "mana": fields}
    if fields or has_artifact_steps:
        validate_artifact_payment_sources(host, seat, response["mana"], exclude_sources)
    if MANA_POOL_PAYMENT_FIELD in response:
        return _pay_from_selected_pool(host, seat, requirements, response, spend_context=spend_context,
            snow_required=snow_required, preserve_source_id=preserve_source_id, exclude_sources=exclude_sources)
    spending_policy = current_mana_spending_policy(host, seat, spend_context)
    activations: list[dict[str, Any]] = []
    payment_window = {"exclude_sources": exclude_sources,
                      "hand_mana_requested": bool(any(requirements.values()) or snow_required)}
    pay_mode = response.get('pay', 'auto')
    if pay_mode == 'auto':
        if fields or has_artifact_steps:
            activations = [dict(row) for row in response["mana"]]
            if any(ARTIFACT_HAND_MANA_STEP not in row for row in activations):
                raise GameRuleError("Automatic payment accepts explicit artifact selections and plans the remaining mana")
            _activate_payment_sources(host, seat, activations, response, spend_context, preserve_source_id,
                                      **payment_window)
        sources = _payment_mana_sources(host, seat, spend_context, exclude_sources, preserve_source_id)
        plan = auto_plan_payment(
            requirements,sources,
            allow_conditional=bool(response.get('allow_conditional_mana',False)) and not host.state.config.strict_mana,
            reserve=normalize_mana_bundle(response.get('reserve')),
            starting_pool=host._spendable_mana_pool(seat,spend_context),
            snow_required=snow_required,starting_snow_pool=host._snow_mana_pool(seat,spend_context),
            spending_policy=spending_policy,funding=_plan_funding(host,seat,sources,spend_context),
        )
        _activate_payment_sources(host, seat, plan.activations, response, spend_context, preserve_source_id)
        activations.extend(plan.activations)
        payment = plan.payment
        snow_payment = plan.snow_payment
    else:
        activations = [dict(item) for item in response.get('mana') or []]
        _activate_payment_sources(host, seat, activations, response, spend_context, preserve_source_id,
                                  **payment_window)
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
    paid = host._apply_mana_spend(seat, combined, spend_context, snow_payment=snow_payment)
    return ManaCostPayment(paid, activations)

def _affordability_mana_sources(host, seat, sources, *, public_hand_mana_bound):
    def selectable(mode):
        if mode.artifact_hand_mana is None:
            return mode
        changes = {}
        if public_hand_mana_bound:
            # An invitation to another payer must depend only on public hand size.
            size = len(host.state.players[seat].zones["hand"])
            changes = {"bundle": normalize_mana_bundle({"C": 2 * size}), "triggered_mana": ()}
        return replace(mode, requires_choice=False, artifact_hand_mana=None, **changes)

    if public_hand_mana_bound:
        # A helper's private hand sources become available only after their
        # authenticated mana window opens. They cannot qualify a public invitation.
        sources = [source for source in sources if not any(mode.exiles_hand_source for mode in source.modes)]
    return [
        replace(source, modes=tuple(selectable(mode) for mode in source.modes))
        if any(mode.artifact_hand_mana is not None for mode in source.modes) else source
        for source in sources
    ]


def cost_is_affordable(
    host, seat: str, requirements: Mapping[str,int], *, exclude_sources: set[str] | None=None,
    spend_context: str | None=None, snow_required: int=0,
    preserve_source_id: str | None = None, public_hand_mana_bound: bool = False,
) -> bool:
    spending_policy = current_mana_spending_policy(host, seat, spend_context)
    try:
        sources = _payment_mana_sources(host, seat, spend_context, exclude_sources, preserve_source_id)
        # Feasibility can use the greatest selectable amount. The payment
        # planner still requires the pilot's explicit refs before any reveal.
        sources = _affordability_mana_sources(host, seat, sources, public_hand_mana_bound=public_hand_mana_bound)
        auto_plan_payment(
            {key:int(requirements.get(key,0)) for key in ('GENERIC','W','U','B','R','G','C')},sources,
            starting_pool=host._spendable_mana_pool(seat,spend_context),snow_required=snow_required,
            starting_snow_pool=host._snow_mana_pool(seat,spend_context),spending_policy=spending_policy,
            funding=_plan_funding(host,seat,sources,spend_context),
        )
        return True
    except ManaPlanError:
        return False
