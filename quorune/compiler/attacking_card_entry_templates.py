from __future__ import annotations

"""Fixed hand, graveyard target, and graveyard source entry with a fresh defender choice."""

import re
from dataclasses import replace

from ..attack_batch_model import QUALIFIED_ATTACK_BATCH_VARIANT
from ..hand_entry_queries import decode_hand_entry_queries
from ..rules.attacking_card_entry import (
    ATTACKING_HAND_ENTRY_VERSION,
    SOURCE_GRAVEYARD_ATTACKING_ENTRY_MECHANIC,
    SOURCE_GRAVEYARD_IDENTITY,
    attacking_entry_choice,
)
from .hand_entry_templates import fixed_private_hand_entry_effect_template
from .optional_payment_templates import fixed_optional_mana_payment_template
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


_SOURCE_GRAVEYARD_ATTACKING = re.compile(
    r"Return this card from your graveyard to the battlefield tapped and attacking"
    r"(?P<counter> with a \+1/\+1 counter on it)?\.?",
    re.I,
)


def fixed_source_graveyard_attacking_entry_template(text):
    match = _SOURCE_GRAVEYARD_ATTACKING.fullmatch(text.strip())
    if match is None:
        return None
    move = {
        "op": "move", "card": "$source", "from": "graveyard", "destination": "battlefield",
        "controller": "$controller", "tapped": True, "source_identity": SOURCE_GRAVEYARD_IDENTITY,
    }
    if match["counter"]:
        move.update(entry_counter="+1/+1", entry_source="$stack")
    return ("fixed-source-graveyard-attacking-entry-v1", (attacking_entry_choice(move),),
        None, (SOURCE_GRAVEYARD_ATTACKING_ENTRY_MECHANIC,))


def source_graveyard_attacking_entry_binding(spec):
    if spec.event != "creature.attacks" or spec.variant not in {
        "controller_attack_batch", "controller_attack_batch_at_least_2",
        "controller_attack_batch_at_least_3", QUALIFIED_ATTACK_BATCH_VARIANT,
    }:
        return spec
    payment = fixed_optional_mana_payment_template(
        spec.body,
        compile_effect=lambda body: fixed_source_graveyard_attacking_entry_template(body) or (None, (), None, ()),
    )
    return replace(spec, active_zone="graveyard") if payment is not None else spec
