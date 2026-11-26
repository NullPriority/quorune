"""Compile the complete one-or-more subtype damage and Treasure trigger."""

from . import regex_working_set as re
from ..creature_subtypes import canonical_creature_subtype
from ..rules.controlled_subtype_damage_batch import AMOUNT_REFERENCE, EVENT, TEMPLATE, VARIANT
from ..rules.controlled_subtype_damage_batch import controlled_subtype_damage_condition
from .oracle_source_text import without_parenthetical_reminder
from .token_templates import fixed_token_creation_effect_template


_PATTERN = re.compile(
    r"Whenever one or more (?P<subtype>[A-Za-z'’ -]+?)s you control deal damage to your opponents, "
    r"(?P<body>you create a Treasure token for each opponent dealt damage\.)",
    re.I,
)


def controlled_subtype_damage_binding_parts(text):
    material = " ".join(without_parenthetical_reminder(text).split())
    match = _PATTERN.fullmatch(material)
    if match is None:
        return None
    subtype = canonical_creature_subtype(match["subtype"])
    if subtype is None:
        return None
    return match["body"], controlled_subtype_damage_condition(subtype)


def controlled_subtype_damage_result(binding, body):
    if (binding.event.value != EVENT or binding.variant != VARIANT or binding.active_zone != "battlefield"
            or body.casefold() != "you create a treasure token for each opponent dealt damage."):
        return None
    template = fixed_token_creation_effect_template("Create a Treasure token.")
    if template is None:
        return None
    return (TEMPLATE, ({**template.effect, "quantity": AMOUNT_REFERENCE},), None, template.mechanics), False
