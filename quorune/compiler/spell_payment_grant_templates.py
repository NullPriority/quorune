from __future__ import annotations

"""Closed printed battlefield spell-payment grants."""

import re

from ..semantic_runtime.spell_payment_grants import SpellPaymentGrantHandler, SpellPaymentGrantSpec
from .cast_cost_modifier_templates import fixed_spell_predicate


def spell_payment_grant_handler(text: str):
    match = re.fullmatch(
        r"(?P<subject>(?:.+? )?spells you cast)(?: from (?P<origin>exile))? "
        r"have (?P<kind>convoke|delve|improvise)\.?",
        " ".join(text.split()), re.IGNORECASE,
    )
    if match is None:
        return None
    predicate = fixed_spell_predicate(match["subject"])
    if predicate is None:
        return None
    kind = match["kind"].casefold()
    origin = match["origin"].casefold() if match["origin"] else None
    try:
        spec = SpellPaymentGrantSpec(kind, predicate, origin)
    except ValueError:
        return None
    handler = SpellPaymentGrantHandler(kind)
    return "fixed-battlefield-spell-payment-grant-v1", {
        "handler_id": handler.handler_id, "schema_version": handler.schema_version, "event": handler.event,
        "requires_complete_card_program": True, "grant": spec.to_dict(),
    }, handler.capability_dependencies
