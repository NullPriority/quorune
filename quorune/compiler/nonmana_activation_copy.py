"""Exact public activation occurrence and optional paid copy result."""

from . import regex_working_set as re
from .fixed_public_event_trigger_bindings import _spec
from ..rules.nonmana_activation_copy_model import ABILITY_ACTIVATION_COPY_CAPABILITY, ABILITY_ACTIVATION_COPY_EVENT
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
from ..rules.nonmana_activation_copy import nonmana_activation_condition, nonmana_activation_copy_payment

VARIANT = "controller_pays_to_copy_nonmana_activation"
BODY = "you may pay {2}. If you do, copy that ability. You may choose new targets for the copy."
TEMPLATE = "fixed-paid-nonmana-activation-copy-v1"


def nonmana_activation_copy_binding(text):
    prefix = "Whenever you activate an ability, if it isn't a mana ability, "
    if re.fullmatch(re.escape(prefix + BODY), text, re.IGNORECASE):
        return _spec(ABILITY_ACTIVATION_COPY_EVENT, VARIANT, BODY, TEMPLATE,
                     "trigger-event-normalized-public-action", condition=nonmana_activation_condition(),
                     capabilities=(ABILITY_ACTIVATION_COPY_CAPABILITY,))
    return None


def nonmana_activation_copy_result(binding, body):
    if binding.variant == VARIANT and body == BODY:
        return TEMPLATE, (nonmana_activation_copy_payment(),), None, (FIXED_EFFECT_PAYMENT_MECHANIC,)
    return None, (), None, ()
