from __future__ import annotations

"""Ordered temporary player-rule productions with independent typed owners."""

import re

from ..rules.cast_cascade_grants import CAST_CASCADE_MECHANIC, CAST_CASCADE_OPERATION, CastCascadeScope
from ..rules.next_cast_counter_protection import (
    NEXT_CAST_PROTECTION_MECHANIC, NEXT_CAST_PROTECTION_OPERATION, NextCastProtectionScope,
)
from ..rules.next_spell_payment import NEXT_SPELL_PAYMENT_MECHANIC, NEXT_SPELL_PAYMENT_OPERATION
from .temporary_cast_restrictions import temporary_cast_restriction_template
from .library_search_rule_templates import temporary_library_search_prohibition_template


_NEXT_CASCADE = re.compile(
    r"The next (?P<kind>creature|noncreature) spell you cast this turn has cascade\.?", re.I,
)


def _temporary_cascade_template(text):
    material = " ".join(text.split()).rstrip(".")
    match = _NEXT_CASCADE.fullmatch(material)
    if match is not None:
        scopes = (CastCascadeScope(match["kind"].casefold()),)
    elif material.casefold() == "the next instant spell and the next sorcery spell you cast this turn each have cascade":
        scopes = (CastCascadeScope.INSTANT, CastCascadeScope.SORCERY)
    elif material.casefold() == "as you cast spells from your hand this turn, they gain cascade":
        scopes = (CastCascadeScope.HAND,)
    else:
        return None
    effect = {"op": CAST_CASCADE_OPERATION, "schema_version": 1, "player": "$controller",
              "scopes": [scope.value for scope in scopes]}
    return "temporary-cast-cascade-grant-v1", (effect,), None, (CAST_CASCADE_MECHANIC,)


_NEXT_CAST_PROTECTION = re.compile(
    r"The next (?:(?P<kind>creature|instant or sorcery) )?spell you cast this turn can['’]t be countered\.?",
    re.IGNORECASE,
)


def _next_cast_protection_template(text):
    match = _NEXT_CAST_PROTECTION.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    scope = {None: NextCastProtectionScope.ANY, "creature": NextCastProtectionScope.CREATURE,
             "instant or sorcery": NextCastProtectionScope.INSTANT_OR_SORCERY}[
        match["kind"].casefold() if match["kind"] else None]
    effect = {"op": NEXT_CAST_PROTECTION_OPERATION, "schema_version": 1,
              "player": "$controller", "scope": scope.value}
    return "next-cast-counter-protection-v1", (effect,), None, (NEXT_CAST_PROTECTION_MECHANIC,)


def temporary_player_rule_template(text):
    if grant := _temporary_cascade_template(text) or _next_cast_protection_template(text):
        return grant
    match = re.fullmatch(r"The next spell you cast this turn has (?P<kind>convoke|improvise)\.?", text.strip(), re.I)
    if match is not None:
        effect = {"op": NEXT_SPELL_PAYMENT_OPERATION, "schema_version": 1, "player": "$controller",
                  "kind": match["kind"].casefold()}
        return "fixed-next-spell-payment-grant-v1", (effect,), None, (NEXT_SPELL_PAYMENT_MECHANIC,)
    return temporary_cast_restriction_template(text) or temporary_library_search_prohibition_template(text)
