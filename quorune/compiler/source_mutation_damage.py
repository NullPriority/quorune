"""Exact source-mutation damage on an ordinary battlefield tap activation."""

from . import regex_working_set as re
from ..rules.source_mutation_damage import source_mutation_damage_template


def source_mutation_damage_activation(ability, text, *, public_timing):
    if (public_timing is not None or ability.zones != ("battlefield",)
            or ability.mana_ability or ability.cost_text != "{T}"
            or ability.sorcery_speed or ability.activation_limit is not None or ability.activation_conditions):
        return None
    if re.fullmatch(r"This creature deals X damage to any target, where X is the number of times this creature has mutated\.",
                    text.strip(), re.IGNORECASE):
        return source_mutation_damage_template()
    return None
