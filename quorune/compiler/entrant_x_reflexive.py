"""Printed entry and paid-X grammar for one reflexive counter family."""

from . import regex_working_set as re
from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC
from ..reflexive_trigger_model import REFLEXIVE_PAYMENT_MECHANIC
from ..rules.entrant_x_reflexive import (
    ENTRANT_X_BODY, ENTRANT_X_MECHANIC, ENTRANT_X_VARIANT, entrant_x_condition, entrant_x_effect,
)


def fixed_public_binding_with_entrant_x(material_line, *, card_name):
    from .fixed_public_event_trigger_bindings import _spec, fixed_public_event_binding_spec

    match = re.fullmatch(r"Whenever another creature you control enters, (?P<body>.+)", material_line, re.I)
    if match is not None and match["body"].casefold() == ENTRANT_X_BODY:
        return _spec(
            "creature.enter", ENTRANT_X_VARIANT, match["body"],
            "fixed-counter-public-zone-trigger-v1", "trigger-event-normalized-zone-change",
            condition=entrant_x_condition(),
        )
    return fixed_public_event_binding_spec(material_line, card_name=card_name)


def entrant_x_binding_result(binding, body):
    if binding.variant != ENTRANT_X_VARIANT or body.casefold() != ENTRANT_X_BODY:
        return None
    return (("entrant-announced-x-reflexive-counters-v1", (entrant_x_effect(),), None,
             (FIXED_EFFECT_PAYMENT_MECHANIC, REFLEXIVE_PAYMENT_MECHANIC, ENTRANT_X_MECHANIC)), True)
