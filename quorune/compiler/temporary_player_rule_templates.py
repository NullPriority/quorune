from __future__ import annotations

"""Ordered temporary player-rule productions with independent typed owners."""

import re

from ..rules.next_spell_payment import NEXT_SPELL_PAYMENT_MECHANIC, NEXT_SPELL_PAYMENT_OPERATION
from .temporary_cast_restrictions import temporary_cast_restriction_template
from .library_search_rule_templates import temporary_library_search_prohibition_template


def temporary_player_rule_template(text):
    match = re.fullmatch(r"The next spell you cast this turn has (?P<kind>convoke|improvise)\.?", text.strip(), re.I)
    if match is not None:
        effect = {"op": NEXT_SPELL_PAYMENT_OPERATION, "schema_version": 1, "player": "$controller",
                  "kind": match["kind"].casefold()}
        return "fixed-next-spell-payment-grant-v1", (effect,), None, (NEXT_SPELL_PAYMENT_MECHANIC,)
    return temporary_cast_restriction_template(text) or temporary_library_search_prohibition_template(text)
