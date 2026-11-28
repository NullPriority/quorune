from __future__ import annotations

from . import regex_working_set as re
from .draw_templates import fixed_draw_effect_template
from .optional_payment_templates import FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC
from .oracle_source_text import without_parenthetical_reminder
from .token_templates import fixed_token_creation_effect_template
from ..event_spell_copy_model import EVENT_SPELL_COPY_MECHANIC
from ..rules.opponent_cast_copy_payment import optional_cast_copy_result
from ..rules.opponent_event_mana_payment import (
    opponent_event_mana_payment_effect, opponent_event_payment_conditions_match,
)


_DECLINE_COPY = re.compile(
    r"They may pay \{(?P<price>[1-9]|10)\}\. If they don['’]t, you may copy that spell\. "
    r"You may choose new targets for the copy\.", re.I,
)


def _unpaid_cast_result(material):
    if match := _DECLINE_COPY.fullmatch(material):
        return int(match["price"]), optional_cast_copy_result(), (EVENT_SPELL_COPY_MECHANIC,)
    match = re.fullmatch(r"You may draw a card unless that player pays \{(?P<price>[1-9]|10)\}\.", material, re.I)
    if match is None:
        return None
    _, (child,), _, mechanics = fixed_draw_effect_template("You may draw a card.")
    return int(match["price"]), child, mechanics


def _unpaid_draw_result(material):
    match = re.fullmatch(
        r"That player may pay \{(?P<price>[1-9]|10)\}\. If the player doesn['’]t, you create a Treasure token\.",
        material, re.I,
    )
    if match is None:
        return None
    _, (child,), _, mechanics = fixed_token_creation_effect_template("Create a Treasure token.").compiled()
    return int(match["price"]), child, mechanics


def opponent_event_mana_result(binding, body):
    if binding.active_zone != "battlefield" or body != binding.body:
        return None
    event = binding.event.value
    result_for_event = {"spell.cast": _unpaid_cast_result, "card.drawn": _unpaid_draw_result}.get(event)
    if result_for_event is None:
        return None
    result = result_for_event(" ".join(without_parenthetical_reminder(body).split()))
    if result is None:
        return None
    price, child, mechanics = result
    if not opponent_event_payment_conditions_match(event, binding.event_condition, child):
        return None
    effect = opponent_event_mana_payment_effect(price, child)
    return "fixed-opponent-event-mana-payment-v1", (effect,), None, (FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC, *mechanics)
