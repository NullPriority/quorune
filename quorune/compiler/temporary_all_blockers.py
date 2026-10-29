from __future__ import annotations

"""Exact target or source instructions that require all able blockers this turn."""

from dataclasses import dataclass
import re

from ..rules.source_references import SourceReferenceSpec
from ..temporary_all_blocker_model import TemporaryAllBlockerSpec


@dataclass(frozen=True, slots=True)
class TemporaryAllBlockerTemplate:
    card_reference: str

    def __post_init__(self):
        if self.card_reference not in {'$target.0', '$source.zone_object'}:
            raise ValueError('Temporary blocking requirements need one target or the sealed source')

    def compiled(self):
        targeted = self.card_reference == '$target.0'
        return ('temporary-all-able-blockers-eot-v1',
            ({'op': 'grant_declaration_restriction_until_end_of_turn',
              'card': self.card_reference, 'restriction': TemporaryAllBlockerSpec().to_dict()},),
            {'zones': ['battlefield'], 'categories': ['permanent'], 'types_any': ['creature'], 'count': 1} if targeted else None,
            ('cr-611-continuous-effects', 'cr-509-declare-blockers-step', *(('cr-115-targets',) if targeted else ())))


def temporary_all_blocker_template(text, *, card_name, allow_source):
    match = re.fullmatch(r'All creatures able to block (?P<subject>.+?) this turn do so\.?', text, re.I)
    if match is None:
        return None
    subject = match['subject']
    if subject.casefold() == 'target creature':
        return TemporaryAllBlockerTemplate('$target.0')
    if allow_source and (subject.casefold() == 'this creature' or SourceReferenceSpec(card_name or 'source').matches(subject)):
        return TemporaryAllBlockerTemplate('$source.zone_object')
    return None
