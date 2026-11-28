"""Compile complete one-or-more controlled subtype damage triggers."""

from . import regex_working_set as re
from ..creature_subtypes import canonical_creature_subtype
from ..rules.controlled_subtype_damage_batch import (
    AMOUNT_REFERENCE, COMBAT_EVENT, COMBAT_MECHANIC, COMBAT_TEMPLATE, COMBAT_VARIANT,
    EVENT, MECHANIC, TEMPLATE, VARIANT, combat_subtype_hand_entry_effect,
    controlled_subtype_combat_damage_condition, controlled_subtype_damage_condition,
)
from .oracle_source_text import without_parenthetical_reminder
from .token_templates import fixed_token_creation_effect_template


_PATTERN = re.compile(
    r"Whenever one or more (?P<subtype>[A-Za-z'’ -]+?)s you control deal damage to your opponents, "
    r"(?P<body>you create a Treasure token for each opponent dealt damage\.)",
    re.I,
)


_COMBAT_HAND_ENTRY = re.compile(
    r"Whenever one or more (?P<subtype>[A-Za-z'’ -]+?)s you control deal combat damage to a player, "
    r"(?P<body>you may put a permanent card with mana value less than or equal to that damage "
    r"from your hand onto the battlefield\.)",
    re.I,
)


def _combat_binding_parts(material):
    match = _COMBAT_HAND_ENTRY.fullmatch(material)
    subtype = canonical_creature_subtype(match["subtype"]) if match else None
    if subtype is None:
        return None
    return (COMBAT_EVENT, COMBAT_VARIANT, match["body"], COMBAT_TEMPLATE, COMBAT_MECHANIC,
            controlled_subtype_combat_damage_condition(subtype))


def controlled_subtype_damage_binding_parts(text):
    material = " ".join(without_parenthetical_reminder(text).split())
    match = _PATTERN.fullmatch(material)
    if match is None:
        return _combat_binding_parts(material)
    subtype = canonical_creature_subtype(match["subtype"])
    if subtype is None:
        return None
    return EVENT, VARIANT, match["body"], TEMPLATE, MECHANIC, controlled_subtype_damage_condition(subtype)


def controlled_subtype_damage_result(binding, body):
    if (binding.event.value, binding.variant, binding.active_zone) == (COMBAT_EVENT, COMBAT_VARIANT, "battlefield"):
        if body != binding.body:
            return None
        return (COMBAT_TEMPLATE, (combat_subtype_hand_entry_effect(),), None, (COMBAT_MECHANIC,)), False
    if (binding.event.value != EVENT or binding.variant != VARIANT or binding.active_zone != "battlefield"
            or body.casefold() != "you create a treasure token for each opponent dealt damage."):
        return None
    template = fixed_token_creation_effect_template("Create a Treasure token.")
    if template is None:
        return None
    return (TEMPLATE, ({**template.effect, "quantity": AMOUNT_REFERENCE},), None, template.mechanics), False
