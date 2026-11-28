"""Recognize closed loyalty damage text on the existing activation pipeline."""

from ..rules.loyalty_damage import (
    LOYALTY_X_DAMAGE_MECHANIC, loyalty_damage_target_schema, loyalty_x_damage_instruction,
)
from ..rules.source_references import SourceReferenceSpec
from ..target_combat_damage_multiplier_model import MULTIPLIER_MECHANIC, multiplier_instruction
from . import regex_working_set as re


_MULTIPLIER = (
    "Choose target creature. Until your next turn, if that creature would deal "
    "combat damage to one of your opponents, it deals triple that damage to that player instead."
)


def loyalty_damage_activation_template(ability, text, *, source_name, public_timing):
    if (public_timing is not None or ability.zones != ("battlefield",) or ability.mana_ability
            or ability.sorcery_speed or ability.activation_conditions or ability.loyalty_delta != 0):
        return None
    if not ability.loyalty_x and ability.cost_text == "0" and text.strip().casefold() == _MULTIPLIER.casefold():
        return (
            "loyalty-target-combat-damage-multiplier-v1", (multiplier_instruction(),),
            loyalty_damage_target_schema(variable=False), (MULTIPLIER_MECHANIC, "cr-115-targets"),
        )
    if ability.loyalty_x and re.fullmatch(
        rf"{SourceReferenceSpec(source_name).regex_pattern} deals X damage to each of up to three targets\.",
        text.strip(), flags=re.IGNORECASE,
    ):
        return (
            "loyalty-x-damage-each-target-v1", (loyalty_x_damage_instruction(),),
            loyalty_damage_target_schema(variable=True), (LOYALTY_X_DAMAGE_MECHANIC, "cr-115-targets", "cr-120-damage"),
        )
    return None


_TURN_CREATURE_COMBAT = re.compile(
    r"(?P<removal>All creatures lose trample until end of turn\.) "
    r"If a creature would deal combat damage to a creature this turn, "
    r"it deals double that damage to that creature instead\.", re.I,
)


def turn_creature_combat_multiplier_template(text):
    match = _TURN_CREATURE_COMBAT.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    from ..target_combat_damage_multiplier_model import turn_multiplier_instruction
    from .temporary_keyword_removal_templates import temporary_keyword_removal_template

    _, removal, targets, mechanics = temporary_keyword_removal_template(
        match["removal"], source_name="this spell", source_is_permanent=False, source_card_types=(),
    )
    return "turn-creature-combat-multiplier-v1", (*removal, turn_multiplier_instruction()), targets, mechanics
