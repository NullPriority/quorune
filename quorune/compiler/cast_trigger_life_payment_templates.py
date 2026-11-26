from __future__ import annotations

"""Lower the exact optional price and counter of the original resolving spell."""

from . import regex_working_set as re
from ..fractional_life_payment_model import FractionalLifePaymentSpec
from ..rules.cast_trigger_life_payment import CastTriggerLifePaymentSpec
from ..rules.source_references import SourceReferenceSpec


_CAST_TRIGGER_LIFE_PAYMENT = re.compile(
    r"Any player may pay (?:(?P<fixed>[1-9][0-9]*) life|(?P<half>half their life, rounded up))\. "
    r"If a player does, counter (?P<source>[^.]+)\.",
    re.IGNORECASE,
)


def cast_trigger_life_payment_body(text: str, *, source_name: str) -> CastTriggerLifePaymentSpec | None:
    if type(source_name) is not str or not source_name:
        return None
    match = _CAST_TRIGGER_LIFE_PAYMENT.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    subject = match["source"]
    if subject.casefold() != "this spell" and not SourceReferenceSpec(source_name).matches(subject):
        return None
    price = FractionalLifePaymentSpec() if match["half"] else int(match["fixed"])
    return CastTriggerLifePaymentSpec(price)


def cast_trigger_life_payment_template(binding, body: str, *, source_name: str):
    from ..rules.cast_trigger_life_payment import (
        cast_trigger_life_payment_condition, cast_trigger_life_payment_effect,
    )

    if (binding.event.value != "spell.cast" or binding.active_zone != "stack"
            or binding.event_condition != cast_trigger_life_payment_condition()):
        return None
    spec = cast_trigger_life_payment_body(body, source_name=source_name)
    if spec is None:
        return None
    return ("fixed-own-cast-life-counter-payment-v1", (cast_trigger_life_payment_effect(spec),),
            None, ())


def binding_with_cast_life_payment(binding, body, *, card_name, effect_template, source_counter_variants):
    cast_offer = cast_trigger_life_payment_template(binding, body, source_name=card_name)
    if cast_offer is not None:
        return cast_offer, False
    from .trigger_result_templates import binding_effect_template

    return binding_effect_template(
        binding, body, card_name=card_name, effect_template=effect_template,
        source_counter_variants=source_counter_variants,
    )
