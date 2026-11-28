from __future__ import annotations

"""Bind a single d20 draw followed by one independent cleanup-rule grant."""

from ..rules.die_roll_draw import (
    HAND_COUNT_TEMPLATE, MECHANIC, controller_hand_count_draw_and_size_instructions, die_draw_hand_size_instructions,
)
from . import regex_working_set as re

_BODY = re.compile(
    r"Roll a d20\. Draw (?:a number of )?cards equal to the result\. "
    r"You have no maximum hand size for the rest of the game\.", re.IGNORECASE,
)


_HAND_COUNT = re.compile(
    r"Draw cards equal to the number of cards in your hand plus one\. "
    r"You have no maximum hand size for the rest of the game\.", re.IGNORECASE,
)


def controller_hand_count_draw_and_size_template(text):
    if _HAND_COUNT.fullmatch(" ".join(text.split())) is None:
        return None
    return HAND_COUNT_TEMPLATE, controller_hand_count_draw_and_size_instructions(), None, ("public-query-effect-amount",)


def combat_die_draw_hand_size_result(binding, body):
    if ((binding.event.value, binding.variant, binding.active_zone)
            != ("damage.dealt.self", "source_combat_damage_player", "battlefield")
            or body != binding.body or _BODY.fullmatch(" ".join(body.split())) is None):
        return None
    return "single-d20-draw-game-hand-size-v1", die_draw_hand_size_instructions(), None, (MECHANIC,)


def equipped_attack_die_result(binding, body):
    from ..rules.equipped_attack_die import ATTACK_CONDITION, ATTACK_EVENT, MECHANICS, equipped_attack_die_effects
    from ..replacement.immutable import FrozenMap

    text = (
        "roll a six-sided die. that creature gets +x/+0 until end of turn, where x is the result. "
        "then if it has the greatest power or is tied for greatest power among creatures on the battlefield, draw a card."
    )
    if ((binding.event.value, binding.variant, binding.active_zone) != (ATTACK_EVENT, "equipped_creature_attacks", "battlefield")
            or FrozenMap(binding.event_condition or {}) != ATTACK_CONDITION
            or body != binding.body or " ".join(body.split()).casefold() != text):
        return None
    return "equipped-attack-d6-growth-and-draw-v1", equipped_attack_die_effects(), None, MECHANICS
