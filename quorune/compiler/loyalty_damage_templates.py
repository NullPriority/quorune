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
