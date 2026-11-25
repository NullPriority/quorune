from __future__ import annotations

"""Ordered temporary player-rule productions with independent typed owners."""

from . import regex_working_set as re

from ..qualified_hexproof import (
    HEXPROOF_COLORS, TURN_COLOR_SPELL_PROTECTION_MECHANIC, TURN_COLOR_SPELL_PROTECTION_OPERATION,
)
from ..public_history_query_model import OPPONENT_CAST_COLOR_DRAW, SPELL_COLOR_HISTORY_FIELD
from ..target_source import (
    TURN_SPELL_SOURCE_COLOR_PROTECTION_MECHANIC, TURN_SPELL_SOURCE_COLOR_PROTECTION_OPERATION,
)

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
from ..draw_redirect_model import (
    DRAW_REDIRECT_MECHANIC, draw_redirect_instruction, draw_redirect_target_schema,
)
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


_TURN_LAND_PLAY_ALLOWANCE = re.compile(
    r"You may play (?:(?P<one>an) additional land|(?:up to )?(?P<many>two|three) additional lands) this turn\.?$",
    re.IGNORECASE,
)


def _temporary_land_play_allowance_template(text):
    from ..rules.land_play_permissions import (
        TURN_LAND_PLAY_ALLOWANCE_FIELD, TURN_LAND_PLAY_ALLOWANCE_MECHANIC, TURN_LAND_PLAY_ALLOWANCE_OPERATION,
    )

    match = _TURN_LAND_PLAY_ALLOWANCE.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    amount = 1 if match["one"] else {"two": 2, "three": 3}[match["many"].casefold()]
    effect = {"op": TURN_LAND_PLAY_ALLOWANCE_OPERATION, "schema_version": 1,
              "player": "$controller", TURN_LAND_PLAY_ALLOWANCE_FIELD: amount}
    return "temporary-additional-land-play-v1", (effect,), None, (TURN_LAND_PLAY_ALLOWANCE_MECHANIC,)


_COLOR_NAME = "(?:" + "|".join(HEXPROOF_COLORS) + ")"
_TURN_COLOR_PROTECTION = re.compile(
    rf"Draw a card if an opponent has cast a (?P<cast_first>{_COLOR_NAME}) or "
    rf"(?P<cast_second>{_COLOR_NAME}) spell this turn\. "
    rf"Spells you control can['’]t be countered this turn\. "
    rf"You and permanents you control gain hexproof from (?P<grant_first>{_COLOR_NAME}) "
    rf"and from (?P<grant_second>{_COLOR_NAME}) until end of turn\.?",
    re.IGNORECASE,
)


_SPELL_SOURCE_COLOR_PROTECTION = re.compile(
    rf"Spells you control can['’]t be countered by (?P<counter_first>{_COLOR_NAME}) or "
    rf"(?P<counter_second>{_COLOR_NAME}) spells this turn, and creatures you control can['’]t be "
    rf"the targets of (?P<target_first>{_COLOR_NAME}) or (?P<target_second>{_COLOR_NAME}) spells this turn\.?",
    re.IGNORECASE,
)


def turn_spell_source_color_protection_template(text):
    match = _SPELL_SOURCE_COLOR_PROTECTION.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    counter = {match["counter_first"].casefold(), match["counter_second"].casefold()}
    target = {match["target_first"].casefold(), match["target_second"].casefold()}
    if len(counter) != 2 or counter != target:
        return None
    effect = {"op": TURN_SPELL_SOURCE_COLOR_PROTECTION_OPERATION, "schema_version": 1,
              "player": "$controller", "colors": [color for name, color in HEXPROOF_COLORS.items() if name in counter]}
    return "turn-spell-source-color-protection-v1", (effect,), None, (TURN_SPELL_SOURCE_COLOR_PROTECTION_MECHANIC,)


def turn_color_spell_protection_template(text):
    match = _TURN_COLOR_PROTECTION.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    cast = {match["cast_first"].casefold(), match["cast_second"].casefold()}
    grant = {match["grant_first"].casefold(), match["grant_second"].casefold()}
    if len(cast) != 2 or cast != grant:
        return None
    colors = [color for name, color in HEXPROOF_COLORS.items() if name in cast]
    effects = (
        {"op": OPPONENT_CAST_COLOR_DRAW, "player": "$controller",
         "colors": colors, SPELL_COLOR_HISTORY_FIELD: 1},
        {"op": TURN_COLOR_SPELL_PROTECTION_OPERATION, "player": "$controller",
         "colors": colors, "schema_version": 2},
    )
    return "fixed-turn-counter-and-color-hexproof-v1", effects, None, (TURN_COLOR_SPELL_PROTECTION_MECHANIC,)


def temporary_player_rule_template(text):
    from .fixed_stack_spell_copy import next_cast_copy_template

    if re.fullmatch(
        r"Until end of turn, if target player would draw a card, instead that player skips that draw and you draw a card\.",
        text.strip(),
        re.I,
    ) is not None:
        return (
            "temporary-target-player-draw-redirect-v1",
            (draw_redirect_instruction(),),
            draw_redirect_target_schema(),
            (DRAW_REDIRECT_MECHANIC, "cr-115-targets"),
        )

    if grant := (turn_color_spell_protection_template(text) or turn_spell_source_color_protection_template(text)
                 or _temporary_cascade_template(text)
                 or _next_cast_protection_template(text)
                 or next_cast_copy_template(text) or _temporary_cast_timing_template(text)
                 or _temporary_land_play_allowance_template(text)):
        return grant
    match = re.fullmatch(r"The next spell you cast this turn has (?P<kind>convoke|improvise)\.?", text.strip(), re.I)
    if match is not None:
        effect = {"op": NEXT_SPELL_PAYMENT_OPERATION, "schema_version": 1, "player": "$controller",
                  "kind": match["kind"].casefold()}
        return "fixed-next-spell-payment-grant-v1", (effect,), None, (NEXT_SPELL_PAYMENT_MECHANIC,)
    return temporary_cast_restriction_template(text) or temporary_library_search_prohibition_template(text)
