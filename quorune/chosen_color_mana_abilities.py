from __future__ import annotations

"""Closed chosen-color output over the shared fixed activation-cost boundary."""

from dataclasses import dataclass, replace
import re
from typing import Mapping

from .chosen_color_mana_model import ChosenColorManaOutput, CHOSEN_COLOR_MANA_HANDLER_ID
from .fixed_mana_abilities import (
    FixedActivatedManaAbilitySpec, FixedManaMode, _without_activation_restriction,
    _without_spend_restriction, fixed_mana_modes_from_effect,
)
from .public_quantity_mana_abilities import compile_mana_activation_costs
from .replacement.immutable import FrozenMap


@dataclass(frozen=True, slots=True)
class ChosenColorActivatedManaAbilitySpec:
    costs: FixedActivatedManaAbilitySpec
    output: ChosenColorManaOutput

    def __post_init__(self):
        if not isinstance(self.costs, FixedActivatedManaAbilitySpec) or not isinstance(self.output, ChosenColorManaOutput):
            raise ValueError('Chosen-color mana requires closed costs and output')
        if (self.costs.modes != (FixedManaMode(green=1),) or self.costs.spend_restriction is not None
                or self.costs.result is not None):
            raise ValueError('Chosen-color cost descriptor carries a competing result')

    def to_dict(self):
        return {'costs': self.costs.to_dict(), 'output': self.output.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'costs', 'output'}:
            raise ValueError('Chosen-color mana ability fields are missing or unknown')
        return cls(FixedActivatedManaAbilitySpec.from_dict(value['costs']), ChosenColorManaOutput.from_dict(value['output']))

    def to_activated_ability(self):
        return replace(self.costs.to_activated_ability(), fixed_mana_outputs=(),
                       dynamic_mana_output=FrozenMap(self.output.to_dict()), mana_spend_restriction=self.output.restriction)


def compile_chosen_color_activated_mana_ability(ability):
    text = _without_activation_restriction(ability, ' '.join(ability.effect_text.split()))
    if text is None:
        return None
    text = _without_spend_restriction(text, ability.mana_spend_restriction)
    if text is None:
        return None
    match = re.fullmatch(r'Add (?:(?P<fixed>(?:\{[WUBRGC]\})+) or )?'
        r'(?P<amount>one|two|three|four|[1-9][0-9]*) mana of the chosen color\.', text, re.IGNORECASE)
    if match is None:
        return None
    words = {'one': 1, 'two': 2, 'three': 3, 'four': 4}
    amount = words.get(match['amount'].casefold()) or int(match['amount'])
    alternative = None if match['fixed'] is None else fixed_mana_modes_from_effect('Add ' + match['fixed'].upper() + '.')[0]
    costs = compile_mana_activation_costs(ability)
    if costs is None:
        return None
    return ChosenColorActivatedManaAbilitySpec(costs, ChosenColorManaOutput(amount, alternative, ability.mana_spend_restriction))


def chosen_color_mana_handler_descriptor(spec):
    return {'handler_id': CHOSEN_COLOR_MANA_HANDLER_ID, 'schema_version': 1, 'event': 'activate', 'ability': spec.to_dict()}
