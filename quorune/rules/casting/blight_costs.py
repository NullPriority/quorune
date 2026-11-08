from __future__ import annotations

"""One typed Blight casting payment and its resumable transaction identity."""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields

from ...cost_payments import ExactManaReplacementReplay, sacrificing_mana_source_ids
from ...mana_mode_effects import mana_damage_event_belongs_to_payment
from ...replacement.model import ReplacementEffectError
from ...replacement.ordering import (
    ReplacementChoiceRequired,
    counter_cost_replacement_progress_matches,
)
from ...additional_cost_vocabulary import BLIGHT_CAST_PAYMENT_FIELD
from ..blight_model import BLIGHT_COUNTER
from ..casting_additional_costs import fixed_counter_additional_cost, fixed_counter_cost_candidates
from .model import CastProposalError


@dataclass(frozen=True, slots=True)
class BlightCastPayment:
    source_ref: str
    option_id: str
    payer_ref: str
    payer_id: str
    payer_incarnation: str
    amount: int

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (
            self.source_ref, self.option_id, self.payer_ref, self.payer_id, self.payer_incarnation,
        )) or type(self.amount) is not int or self.amount <= 0:
            raise ReplacementEffectError("Blight cast payment identity is malformed")

    def to_dict(self):
        return asdict(self)

    def event_id(self, payment_id):
        return (
            f"counter.cost:{payment_id}:{self.source_ref}:blight:"
            f"{self.payer_ref}:{self.payer_id}:{self.payer_incarnation}:{self.amount}"
        )

    def matches_payer(self, payer):
        return (payer.ref, payer.object_id, payer.logical_object_id) == (
            self.payer_ref, self.payer_id, self.payer_incarnation,
        )


def blight_cast_payment_from_response(response):
    if BLIGHT_CAST_PAYMENT_FIELD not in response:
        return None
    raw = response[BLIGHT_CAST_PAYMENT_FIELD]
    if not isinstance(raw, Mapping) or set(raw) != {field.name for field in fields(BlightCastPayment)}:
        raise ReplacementEffectError("Blight cast payment fields are malformed")
    payment = BlightCastPayment(**raw)
    if (
        response.get("card") != payment.source_ref
        or response.get("counter_cost_card") != payment.payer_ref
        or response.get("cost_option") not in (None, payment.option_id)
    ):
        raise ReplacementEffectError("Blight cast payment does not match the command")
    return payment


def payable_blight_cast_candidates(host, seat, cost, requirements, *, spend_context, exclude_sources, snow_required=0):
    refs = fixed_counter_cost_candidates(host, actor=seat, cost=cost)
    if not refs:
        return ()
    sacrificing = sacrificing_mana_source_ids(
        host, seat, refs, spend_context=spend_context, exclude_sources=exclude_sources,
    )
    return tuple(ref for ref in refs if ref not in sacrificing or host._cost_is_affordable(
        seat, requirements, spend_context=spend_context, exclude_sources=exclude_sources,
        preserve_source_id=sacrificing[ref],
        **({"snow_required": snow_required} if snow_required else {}),
    ))


def prepare_blight_cast_payment(host, proposal, selected_option, response):
    saved = blight_cast_payment_from_response(response)
    costs = selected_option.get("additional_costs") or ()
    cost = fixed_counter_additional_cost(costs[0]) if len(costs) == 1 else None
    if cost is None or cost.schema_version != 2:
        if saved is not None:
            raise CastProposalError("The saved Blight branch changed", reason="stale_blight_cast_payment")
        return None
    if selected_option.get("kind") != "additional_alternative":
        raise CastProposalError("The Blight price requires its binary group", reason="blight_cast_group")
    ref = response.get(cost.choice_field)
    payer = host._resolve_object(proposal.seat, ref, zones={"battlefield"}, controlled_only=True)
    payment = BlightCastPayment(
        proposal.card_ref, proposal.cost_option_id, payer.ref, payer.object_id,
        payer.logical_object_id, cost.amount,
    )
    if saved is not None and saved != payment:
        raise CastProposalError("The saved Blight payer or price changed", reason="stale_blight_cast_payment")
    return payment


class BlightCastPaymentChoiceRequired(ReplacementChoiceRequired):
    def __init__(self, choice, payment):
        super().__init__(batch=choice.batch, effects=choice.effects, pending=choice.pending)
        self.payment = payment


@contextmanager
def blight_cast_replacement_scope(payment):
    try:
        yield
    except ReplacementChoiceRequired as choice:
        if payment is None:
            raise
        raise BlightCastPaymentChoiceRequired(choice, payment) from choice


def blight_cast_mana_response(payment, response):
    journal = response.get("_mana_replacement_selections")
    if payment is None or not isinstance(journal, Mapping):
        return response, None
    replay = ExactManaReplacementReplay(journal, response.get("_mana_payment_id"))
    return {**response, "_mana_replacement_selections": replay}, replay


def blight_cast_prior_mana_event_ids(response):
    if blight_cast_payment_from_response(response) is None:
        return set()
    journal = response.get("_mana_replacement_selections")
    if not isinstance(journal, Mapping):
        return set()
    payment_id = response.get("_mana_payment_id")
    return {key for key in journal if mana_damage_event_belongs_to_payment(key, payment_id)}


def blight_cast_cost_event_matches(batch, effects, response, *, seat):
    payment = blight_cast_payment_from_response(response)
    if payment is None:
        return False
    event, = batch.events
    payload = event.payload
    affected = event.affected_object
    target_types = payload.get("target_types")
    if not (
        event.event_id == payment.event_id(response.get("_mana_payment_id"))
        and not event.children and event.entry_scope is None
        and affected is not None and affected.controller == seat and affected.object_id == payment.payer_id
        and payload.get("target_controller") == seat and payload.get("target_zone") == "battlefield"
        and isinstance(target_types, (list, tuple)) and "creature" in target_types
        and payload.get("prospective_subject") is not True
        and payload.get("target_logical_object_id") == payment.payer_incarnation
        and payload.get("counter_name") == BLIGHT_COUNTER and payload.get("source") == payment.source_ref
        and type(payload.get("requested_amount")) is int and payload["requested_amount"] == payment.amount
    ):
        return False
    journal = response.get("_mana_replacement_selections") or {}
    selections = journal.get(event.event_id, ()) if isinstance(journal, Mapping) else None
    if not isinstance(selections, (list, tuple)) or any(type(value) is not str or not value for value in selections):
        return False
    return counter_cost_replacement_progress_matches(batch, effects, selections, payment.amount)
