"""Compile complete single-creature attack-if-able instructions for this turn."""
from dataclasses import dataclass
from . import regex_working_set as re
from ..temporary_attack_requirement_model import TemporaryAttackRequirementSpec


@dataclass(frozen=True, slots=True)
class TemporaryAttackRequirementTemplate:
    def compiled(self):
        return ('temporary-target-attack-each-combat-this-turn-v1',
            ({'op': 'grant_declaration_restriction_until_end_of_turn',
              'card': '$target.0', 'restriction': TemporaryAttackRequirementSpec().to_dict()},),
            {'zones': ['battlefield'], 'categories': ['permanent'], 'types_any': ['creature'], 'count': 1},
            ('cr-611-continuous-effects', 'cr-508-declare-attackers-step', 'cr-115-targets'))


def temporary_attack_requirement_template(text):
    if re.fullmatch(r'Target creature attacks this turn if able\.?', text, re.I):
        return TemporaryAttackRequirementTemplate()
    return None
