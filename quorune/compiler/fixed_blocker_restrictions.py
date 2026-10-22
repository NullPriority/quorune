from __future__ import annotations

"""One exact target creature and a fixed blocker subtype, color union, or power bound."""

from dataclasses import dataclass
import re

from ..creature_subtypes import canonical_creature_subtype_surface
from ..fixed_blocker_restrictions import FixedBlockerRestrictionSpec
from .direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec


@dataclass(frozen=True, slots=True)
class FixedBlockerRestrictionTemplate:
    spec: FixedBlockerRestrictionSpec
    target: DirectPermanentTargetSpec

    def compiled(self):
        return ('temporary-target-fixed-blocker-'+self.spec.slug+'-v1',
            ({'op': 'grant_declaration_restriction_until_end_of_turn', 'card': '$target.0',
                'restriction': self.spec.to_dict()},), self.target.to_target_schema(),
            ('cr-115-targets', 'cr-611-continuous-effects', 'cr-509-declare-blockers-step'))


def fixed_blocker_restriction_template(text):
    match = re.fullmatch(r"(?P<subject>Target creature(?: you control)?) can't be blocked (?P<tail>.+)\.",
        ' '.join(text.strip().split()), re.I)
    if match is None:
        return None
    tail = match['tail']
    spec = None
    if subtype := re.fullmatch(r'(?P<except>this turn except )?by (?P<subtype>[A-Za-z][A-Za-z -]*?)(?P<end> this turn)?', tail, re.I):
        if bool(subtype['except']) == bool(subtype['end']):
            return None
        kind = canonical_creature_subtype_surface(subtype['subtype'])
        if kind is not None:
            spec = FixedBlockerRestrictionSpec('subtype', kind, bool(subtype['except']))
    if color := re.fullmatch(r'this turn except by artifact creatures and/or (?P<color>white|blue|black|red|green) creatures', tail, re.I):
        symbol = {'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G'}[color['color'].casefold()]
        spec = FixedBlockerRestrictionSpec('artifact_or_color', symbol, True)
    if power := re.fullmatch(r'by creatures with power (?P<maximum>\d+) or less this turn', tail, re.I):
        spec = FixedBlockerRestrictionSpec('power_at_most', int(power['maximum']))
    target = direct_permanent_target_spec(match['subject'])
    return FixedBlockerRestrictionTemplate(spec, target) if spec is not None and target is not None else None
