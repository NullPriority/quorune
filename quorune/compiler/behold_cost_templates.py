from __future__ import annotations

"""Source-pinned fixed Behold cost leaves and their exact explanatory reminder."""

from dataclasses import dataclass
from . import regex_working_set as re
from .creature_subtypes import canonical_creature_subtype
from ..rules.behold_additional_cost import FixedBeholdAdditionalCost


@dataclass(frozen=True, slots=True)
class FixedBeholdAdditionalCostTemplate:
    subtype: str

    @property
    def template_id(self):
        return 'spell-additional-cost-fixed-behold-v1'

    @property
    def descriptor(self):
        return FixedBeholdAdditionalCost(self.subtype).to_descriptor()


def fixed_behold_cost_leaf(text):
    match = re.fullmatch(r'behold (?P<article>a|an) (?P<subtype>[A-Za-z][A-Za-z\'-]*(?: [A-Za-z][A-Za-z\'-]*)?)', text.strip(), re.I)
    if match is None:
        return None
    subtype = canonical_creature_subtype(match['subtype'])
    if subtype is None or match['article'].casefold() != ('an' if subtype[0] in 'aeiou' else 'a'):
        return None
    return FixedBeholdAdditionalCostTemplate(subtype)


def without_fixed_behold_reminder(text):
    match = re.fullmatch(
        r'(?P<cost>As an additional cost to cast this spell, behold (?P<article>a|an) '
        r'(?P<subtype>[A-Za-z][A-Za-z\'-]*(?: [A-Za-z][A-Za-z\'-]*)?) or pay '
        r'(?:\{(?:0|[1-9]\d*|[WUBRGC])\})+\.) '
        r'\(To behold (?P=article) (?P=subtype), choose (?P=article) (?P=subtype) you control '
        r'or reveal (?P=article) (?P=subtype) card from your hand\.\)', text.strip(), re.I)
    return match['cost'] if match is not None else text
