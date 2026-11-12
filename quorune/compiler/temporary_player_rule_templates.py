from __future__ import annotations

"""Ordered temporary player-rule productions with independent typed owners."""

import re

from ..rules.cast_cascade_grants import CAST_CASCADE_MECHANIC, CAST_CASCADE_OPERATION, CastCascadeScope
from ..rules.next_cast_counter_protection import (
    NEXT_CAST_PROTECTION_MECHANIC, NEXT_CAST_PROTECTION_OPERATION, NextCastProtectionScope,
)
from ..rules.next_spell_payment import NEXT_SPELL_PAYMENT_MECHANIC, NEXT_SPELL_PAYMENT_OPERATION
from ..rules.temporary_cast_timing import (
    TEMPORARY_CAST_TIMING_FIELD, TEMPORARY_CAST_TIMING_MECHANIC, TEMPORARY_CAST_TIMING_OPERATION,
    TemporaryCastTimingScope,
)
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


_CAST_TIMING_SCOPES = {
    "you may cast spells this turn as though they had flash": TemporaryCastTimingScope.SPELLS,
    "you may cast creature spells this turn as though they had flash": TemporaryCastTimingScope.CREATURE_SPELLS,
    "you may cast sorcery spells this turn as though they had flash": TemporaryCastTimingScope.SORCERY_SPELLS,
    "the next sorcery spell you cast this turn can be cast as though it had flash": TemporaryCastTimingScope.NEXT_SORCERY,
    "the next creature card you play this turn can be played as though it had flash": TemporaryCastTimingScope.NEXT_CREATURE_CARD,
}


def _temporary_cast_timing_template(text):
    material = " ".join(text.casefold().split()).removesuffix(" (it can be cast any time you could cast an instant.)")
    scope = _CAST_TIMING_SCOPES.get(material.removesuffix("."))
    if scope is None:
        return None
    effect = {"op": TEMPORARY_CAST_TIMING_OPERATION, "schema_version": 1, "player": "$controller",
              TEMPORARY_CAST_TIMING_FIELD: scope.value}
    return "temporary-controller-cast-timing-v1", (effect,), None, (TEMPORARY_CAST_TIMING_MECHANIC,)


def temporary_player_rule_template(text):
    from .fixed_stack_spell_copy import next_cast_copy_template

    if grant := (_temporary_cascade_template(text) or _next_cast_protection_template(text)
                 or next_cast_copy_template(text) or _temporary_cast_timing_template(text)):
        return grant
    match = re.fullmatch(r"The next spell you cast this turn has (?P<kind>convoke|improvise)\.?", text.strip(), re.I)
    if match is not None:
        effect = {"op": NEXT_SPELL_PAYMENT_OPERATION, "schema_version": 1, "player": "$controller",
                  "kind": match["kind"].casefold()}
        return "fixed-next-spell-payment-grant-v1", (effect,), None, (NEXT_SPELL_PAYMENT_MECHANIC,)
    return temporary_cast_restriction_template(text) or temporary_library_search_prohibition_template(text)
