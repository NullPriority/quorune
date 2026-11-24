"""Use native object-payment legality and movement for one Ward sacrifice."""

from __future__ import annotations

from dataclasses import replace
from functools import partial

from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..rules.ward_sacrifice import ward_sacrifice_is_eligible
from .fixed_effect_payment import complete_fixed_payment_cost, prepare_fixed_payment_cost


def _cost(payer):
    return FixedEffectPaymentSpec(
        "sacrifice", predicate=ObjectQuerySpec(zones=("battlefield",), controller=payer),
    )


def prepare_ward_sacrifice(effect, context, scope):
    requirement = partial(ward_sacrifice_is_eligible, scope, payer=context.actor)
    prepared = prepare_fixed_payment_cost(_cost(context.actor), effect, context, object_filter=requirement)
    request = prepared.request
    return replace(prepared, request=replace(
        request,
        prompt="Sacrifice one eligible permanent to pay Ward, or choose none to decline.",
        public_context=FrozenMap({**dict(request.public_context), "target_stack": effect["stack"]}),
    ))


def complete_ward_sacrifice(effect, response, query, scope, *, payer):
    requirement = partial(ward_sacrifice_is_eligible, scope, payer=payer)
    return complete_fixed_payment_cost(_cost(payer), effect, response, query, object_filter=requirement)
