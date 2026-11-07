from __future__ import annotations

"""Authenticated, staged Assist casting through the ordinary mana owner."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ...cost_payments import ExactManaReplacementReplay
from ...errors import GameRuleError
from ...mana_mode_effects import mana_damage_event_belongs_to_payment, mana_sacrifice_payment_event_id
from ...replacement.immutable import FrozenMap, thaw_value
from ...replacement.ordering import ReplacementChoiceRequired, replacement_choice_payload
from ...semantic_runtime.mana_spending import current_mana_spending_policy
from ...util import pay_mana_from_pool
from ..action_proposals import thaw_json
from .assist_costs import ASSIST_HELPER_FIELD
from .assist_frame import (
    ASSIST_CASTER_PAYMENT_FIELD, ASSIST_DECISION_KIND, ASSIST_FRAME_FIELD,
    ASSIST_PREVIEW_FIELD, AssistCastFrame, AssistManaStep, PendingAssistReplacement, assist_mana_bundle,
)


class AssistCastStart(Exception):
    def __init__(self, frame):
        super().__init__("Assist needs its authenticated mana windows")
        self.frame = frame


class _AssistPreviewReady(Exception):
    def __init__(self, view):
        super().__init__("Assist payment preview complete")
        self.view = view


@dataclass(frozen=True, slots=True)
class _AssistView:
    announcement: dict
    requirements: dict
    modes: tuple
    helper_pool: dict
    helper_policy: object
    caster_pool: dict
    caster_policy: object

    def remaining(self, bundle):
        price = dict(self.requirements)
        price["GENERIC"] -= sum(bundle.values())
        if price["GENERIC"] < 0:
            raise GameRuleError("Assist cannot pay more than the generic price")
        return price


def prepare_assist_cast(host, proposal, selected_option, response):
    from ...mana_payment_continuations import priority_payment_frame

    helper = selected_option.get(ASSIST_HELPER_FIELD)
    raw = response.get(ASSIST_FRAME_FIELD)
    if helper is None:
        if raw is not None:
            raise GameRuleError("The saved Assist option changed")
        return None
    if raw is None:
        sealed = {key: value for key, value in response.items() if key not in {"proposal_fingerprint", "expiry_revision"}}
        if any(key in sealed for key in ("mana", "payment", "snow_payment", "reserve")):
            raise GameRuleError("Assist announces the helper before either mana window")
        if thaw_json(proposal.details).get("during_resolution"):
            raise GameRuleError("Assist requires the ordinary casting decision owner")
        raise AssistCastStart(AssistCastFrame(
            caster=proposal.seat, helper=helper, source_ref=proposal.card_ref,
            fingerprint=proposal.fingerprint, priority_frame=priority_payment_frame(host), cast_response=sealed,
        ))
    frame = AssistCastFrame.from_dict(raw)
    if (
        frame.caster != proposal.seat or frame.helper != helper or frame.source_ref != proposal.card_ref
        or frame.fingerprint != proposal.fingerprint or thaw_value(frame.priority_frame) != priority_payment_frame(host)
        or frame.helper not in host.active_seats
    ):
        raise GameRuleError("The locked Assist cast changed before payment")
    return frame


def _available_steps(host, seat, spend_context, excluded):
    modes = {}
    for source in host.available_mana_sources(seat, spend_context=spend_context):
        if source.object_id in excluded:
            continue
        for mode in source.modes:
            if mode.requires_choice or (mode.conditional and not (
                mode.restriction and host._mana_restriction_allows(mode.restriction, spend_context)
            )):
                continue
            step = AssistManaStep(seat, source.ref, mode.bundle, mode.tap_source)
            identity = (step.source, tuple(sorted(step.bundle.items())), step.tap_source)
            modes.setdefault(identity, (step, source.name))
    return tuple(modes.values())


def _apply_step(host, frame, step, index, spend_context, excluded):
    reserved = excluded if step.seat == frame.caster else set()
    if not any(candidate.activation() == step.activation()
               for candidate, _name in _available_steps(host, step.seat, spend_context, reserved)):
        raise GameRuleError("The selected Assist mana mode is no longer available")
    payment_id = frame.step_payment_id(index)
    sacrifice_id = mana_sacrifice_payment_event_id(payment_id, step.source)
    if any(event_id != sacrifice_id and not mana_damage_event_belongs_to_payment(event_id, payment_id)
           for event_id in step.replacements):
        raise GameRuleError("Assist replacement history names a different mana activation")
    replay = ExactManaReplacementReplay(thaw_value(step.replacements), payment_id, extra_event_ids={sacrifice_id})
    host._activate_mana_plan(
        step.seat, [step.activation()], spend_context=spend_context,
        payment_id=payment_id, replacement_selections_by_event=replay,
    )
    replay.validate()


def apply_assist_cast_mana(host, frame, proposal, card, response, *, spend_context, excluded):
    for index, step in enumerate(frame.plans):
        _apply_step(host, frame, step, index, spend_context, excluded)
    if frame.pending is not None:
        _apply_step(host, frame, frame.pending.step, len(frame.plans), spend_context, excluded)
    if response.get(ASSIST_PREVIEW_FIELD) is not True:
        return
    actor_excluded = excluded if frame.actor == frame.caster else set()
    raise _AssistPreviewReady(_AssistView(
        announcement={"caster": frame.caster, "helper": frame.helper, "spell": host.display_name(card.object_id),
                      "targets": list(proposal.targets), "stage": frame.stage,
                      "mana_activations": [{"seat": step.seat, **step.activation()} for step in frame.plans]},
        requirements=dict(thaw_json(proposal.requirements)),
        modes=_available_steps(host, frame.actor, spend_context, actor_excluded) if frame.stage.endswith("_mana") else (),
        helper_pool=host._spendable_mana_pool(frame.helper, spend_context),
        helper_policy=current_mana_spending_policy(host, frame.helper, spend_context),
        caster_pool=host._spendable_mana_pool(frame.caster, spend_context),
        caster_policy=current_mana_spending_policy(host, frame.caster, spend_context),
    ))


def commit_assist_cast_payment(host, frame, response, requirements, spend_context):
    if frame.stage != "caster_payment" or frame.pending is not None:
        raise GameRuleError("Assist has unfinished mana or contribution choices")
    helper_payment = thaw_value(frame.contribution)
    caster_payment = thaw_value(assist_mana_bundle(response.get(ASSIST_CASTER_PAYMENT_FIELD)))
    generic = sum(helper_payment.values())
    if generic > requirements["GENERIC"]:
        raise GameRuleError("Assist cannot pay more than the generic price")
    helper_spent, _ = host._pay_for_cost(
        frame.helper, {"GENERIC": generic}, {"pay": "manual", "payment": helper_payment},
        spend_context=spend_context,
    )
    caster_spent, _ = host._pay_for_cost(
        frame.caster, {**requirements, "GENERIC": requirements["GENERIC"] - generic},
        {"pay": "manual", "payment": caster_payment}, spend_context=spend_context,
    )
    return helper_spent, caster_spent


def _preview(host, frame):
    response = {**thaw_value(frame.cast_response), ASSIST_FRAME_FIELD: frame.to_dict(), ASSIST_PREVIEW_FIELD: True}
    try:
        with host.transaction():
            host._cast(frame.caster, response)
            raise GameRuleError("Assist preview unexpectedly committed")
    except _AssistPreviewReady as preview:
        return preview.view


def _preview_payment(pool, price, policy, raw=None):
    payment = thaw_value(assist_mana_bundle(raw)) if raw is not None else None
    try:
        _, spent = pay_mana_from_pool(pool, price, payment=payment, spending_policy=policy)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    return FrozenMap({color: amount for color, amount in spent.items() if amount})


def _helper_limit(view):
    maximum = min(view.requirements["GENERIC"], sum(view.helper_pool.values()))
    low, high = 0, maximum
    while low < high:
        amount = (low + high + 1) // 2
        try:
            _preview_payment(view.helper_pool, {"GENERIC": amount}, view.helper_policy)
        except GameRuleError:
            high = amount - 1
        else:
            low = amount
    return low


def _assist_public(view):
    return {**view.announcement, "requirements": view.requirements}


def issue_assist_choice(host, frame, view=None):
    view = _preview(host, frame) if view is None else view
    public = _assist_public(view)
    payload = {**public, "assist_public": public}
    if frame.stage.endswith("_mana"):
        actions = [
            {"id": f"assist:mana:{index}", "action": "choose", "label": f"Activate {name} for mana",
             "assist_step": step.to_dict()}
            for index, (step, name) in enumerate(view.modes)
        ]
        actions.append({"id": "assist:finish-mana", "action": "choose", "label": "Finish mana abilities",
                        "assist_finish_mana": True})
    elif frame.stage == "helper_payment":
        maximum = _helper_limit(view)
        actions = [{"id": "assist:contribute", "action": "choose", "label": "Choose generic mana to contribute",
                    "choice_schema": {"assist_amount": {"type": "integer", "minimum": 0, "maximum": maximum}}}]
        payload["maximum_contribution"] = maximum
    else:
        price = view.remaining(frame.contribution)
        try:
            _preview_payment(view.caster_pool, price, view.caster_policy)
        except GameRuleError:
            # The voluntary payment was insufficient. The original priority state
            # still holds every spell, source and resource.
            host._grant_priority(frame.caster)
            return
        payload["remaining_requirements"] = price
        payload["helper_contribution"] = sum(frame.contribution.values())
        actions = [{"id": "assist:pay", "action": "choose", "label": "Pay the remaining price",
                    "assist_pay": True}]
    host.permissions.issue(
        kind=ASSIST_DECISION_KIND, role="pilot", actors=[frame.actor], allowed_actions=["choose"],
        payload_by_actor={frame.actor: {**payload, "legal_actions": actions}}, continuation=frame.to_dict(),
    )


def _origin_event_id(required, frame, step):
    from ...replacement_decisions import _mana_damage_event_id

    payment_id = frame.step_payment_id(len(frame.plans))
    events = required.batch.events
    if len(events) == 1 and events[0].kind == "zone.change":
        event = events[0]
        if (event.payload.get("origin") != "battlefield" or event.payload.get("object_ref") != step.source
                or event.affected_object is None or event.affected_object.controller != step.seat):
            raise GameRuleError("Assist sacrifice replacement changed its original payer or source")
        return mana_sacrifice_payment_event_id(payment_id, step.source)
    event_id = _mana_damage_event_id(required.batch)
    if not mana_damage_event_belongs_to_payment(event_id, payment_id):
        raise GameRuleError("Assist damage replacement belongs to another mana activation")
    return event_id


def _try_assist_step(host, frame, step):
    proposed = replace(frame, pending=PendingAssistReplacement(step, "proposed"))
    try:
        view = _preview(host, proposed)
    except ReplacementChoiceRequired as required:
        event_id = _origin_event_id(required, frame, step)
        pending = replace(proposed, pending=PendingAssistReplacement(step, event_id))
        chooser = required.pending.choice.chooser
        public = _assist_public(_preview(host, frame))
        context = replacement_choice_payload(required.pending, required.effects)
        host.permissions.issue(
            kind=ASSIST_DECISION_KIND, role="pilot", actors=[chooser], allowed_actions=["choose"],
            payload_by_actor={chooser: {**context, "assist_public": public}},
            continuation=pending.to_dict(),
        )
        return
    issue_assist_choice(host, replace(frame, plans=(*frame.plans, step)), view)


def _complete_assist_replacement(host, decision, frame, response):
    from ...replacement_decisions import _replacement_selection

    try:
        _preview(host, frame)
    except ReplacementChoiceRequired as required:
        event_id = _origin_event_id(required, frame, frame.pending.step)
        if event_id != frame.pending.event_id or decision.actors != [required.pending.choice.chooser]:
            raise GameRuleError("Assist replacement authority changed")
        selection = _replacement_selection(response, required.pending, error_type=GameRuleError)
    else:
        raise GameRuleError("The saved Assist replacement is no longer available")
    journal = thaw_value(frame.pending.step.replacements)
    journal.setdefault(event_id, []).append(selection)
    _try_assist_step(host, replace(frame, pending=None), replace(frame.pending.step, replacements=journal))


def _validate_choice_fields(response, *fields):
    if set(response).difference(fields, {"action", "label", "choice_schema"}):
        raise GameRuleError("Assist command fields do not match the current window")


def complete_assist_choice(host, decision):
    from ...mana_payment_continuations import resume_mana_choice_capable_priority_action

    frame = AssistCastFrame.from_dict(decision.continuation)
    if len(decision.actors) != 1 or set(decision.responses) != set(decision.actors):
        raise GameRuleError("Assist requires one authenticated decision")
    response = decision.responses[decision.actors[0]]
    if frame.pending is not None:
        _validate_choice_fields(response, "replacement", "replacement_event", "prevention_allocation", "replacement_objects")
        _complete_assist_replacement(host, decision, frame, response)
        return
    if decision.actors != [frame.actor]:
        raise GameRuleError("This Assist window belongs to another player")
    if frame.stage.endswith("_mana"):
        if response.get("assist_finish_mana") is True:
            _validate_choice_fields(response, "assist_finish_mana")
            issue_assist_choice(host, replace(frame, stage="caster_mana" if frame.stage == "helper_mana" else "helper_payment"))
            return
        _validate_choice_fields(response, "assist_step")
        step = AssistManaStep.from_dict(response.get("assist_step"))
        if step.seat != frame.actor or step.replacements:
            raise GameRuleError("Assist mana commands cannot supply another payer or replacement history")
        _try_assist_step(host, frame, step)
        return
    view = _preview(host, frame)
    if frame.stage == "helper_payment":
        _validate_choice_fields(response, "assist_amount", "payment")
        amount = response.get("assist_amount")
        if type(amount) is not int or not 0 <= amount <= _helper_limit(view):
            raise GameRuleError("Assist contribution exceeds the payable generic price")
        bundle = _preview_payment(view.helper_pool, {"GENERIC": amount}, view.helper_policy, response.get("payment"))
        issue_assist_choice(host, replace(frame, stage="caster_payment", contribution=bundle))
        return
    _validate_choice_fields(response, "assist_pay", "payment")
    if response.get("assist_pay") is not True:
        raise GameRuleError("The caster must confirm the remaining Assist payment")
    bundle = _preview_payment(view.caster_pool, view.remaining(frame.contribution), view.caster_policy, response.get("payment"))
    resume_mana_choice_capable_priority_action(
        host, seat=frame.caster, action="cast", response={**thaw_value(frame.cast_response),
            ASSIST_FRAME_FIELD: frame.to_dict(), ASSIST_CASTER_PAYMENT_FIELD: thaw_value(bundle)},
    )
