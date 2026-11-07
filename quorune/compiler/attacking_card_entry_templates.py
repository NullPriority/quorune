from __future__ import annotations

"""Fixed private hand and declared graveyard entry with a fresh defender choice."""

import re

from ..hand_entry_queries import decode_hand_entry_queries
from ..rules.attacking_card_entry import ATTACKING_HAND_ENTRY_VERSION, attacking_entry_choice
from .hand_entry_templates import fixed_private_hand_entry_effect_template
from .reanimation_templates import fixed_target_reanimation_effect_template


_ATTACKING = re.compile(r"(?P<move>(?:You may put|Return|Put) .+? the battlefield) tapped and attacking\.?", re.I)


def fixed_attacking_card_entry_template(text):
    match = _ATTACKING.fullmatch(text.strip())
    if match is None:
        return None
    tapped_text = match["move"] + " tapped."
    hand = fixed_private_hand_entry_effect_template(tapped_text)
    if hand is not None:
        effect, = hand.effects
        queries = decode_hand_entry_queries(effect["query"])
        if not all("creature" in query.types_all for query in queries):
            return None
        effect = {**effect, "schema_version": ATTACKING_HAND_ENTRY_VERSION, "attacking_entry": True}
        return (hand.template_id.removesuffix("-v1") + "-attacking-v3", (effect,), None, hand.mechanics)

    reanimation = fixed_target_reanimation_effect_template(tapped_text)
    if reanimation is None or reanimation.owner_controls:
        return None
    template, effects, schema, mechanics = reanimation.compiled()
    if schema.get("types_any") != ["creature"] and "creature" not in schema.get("types_all", ()):
        return None
    move = {**effects[0], "op": "move", "from": "graveyard", "destination": "battlefield"}
    return (template + "-attacking-v1", (attacking_entry_choice(move),), schema, mechanics)
