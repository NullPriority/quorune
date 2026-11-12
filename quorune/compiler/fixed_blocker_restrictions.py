from __future__ import annotations

"""Fixed blocker predicates for one targeted creature or the original source."""

from dataclasses import dataclass
import re

from ..creature_subtypes import canonical_creature_subtype_surface
from ..fixed_blocker_restrictions import FixedBlockerRestrictionSpec
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec


@dataclass(frozen=True, slots=True)
class FixedBlockerRestrictionTemplate:
    spec: FixedBlockerRestrictionSpec
    target: DirectPermanentTargetSpec | None

    def compiled(self):
        if self.target is None:
            label, card, schema, mechanics = 'source', '$source.zone_object', None, ()
        else:
            label, card, schema, mechanics = (
                'target', '$target.0', self.target.to_target_schema(), ('cr-115-targets',),
            )
        return (
            f'temporary-{label}-fixed-blocker-{self.spec.slug}-v{self.spec.schema_version}',
            ({'op': 'grant_declaration_restriction_until_end_of_turn', 'card': card,
              'restriction': self.spec.to_dict()},),
            schema, (*mechanics, 'cr-611-continuous-effects', 'cr-509-declare-blockers-step'),
        )


def _blocker_spec(tail: str) -> FixedBlockerRestrictionSpec | None:
    subtype = re.fullmatch(
        r'(?P<except>this turn except )?by (?P<subtype>[A-Za-z][A-Za-z -]*?)(?P<end> this turn)?',
        tail, re.I,
    )
    if subtype is not None and bool(subtype['except']) != bool(subtype['end']):
        kind = canonical_creature_subtype_surface(subtype['subtype'])
        if kind is not None:
            return FixedBlockerRestrictionSpec('subtype', kind, bool(subtype['except']))
    if color := re.fullmatch(r'this turn except by artifact creatures and/or (?P<color>white|blue|black|red|green) creatures', tail, re.I):
        symbol = {'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G'}[color['color'].casefold()]
        return FixedBlockerRestrictionSpec('artifact_or_color', symbol, True)
    if power := re.fullmatch(r'by creatures with power (?P<maximum>\d+) or less this turn', tail, re.I):
        return FixedBlockerRestrictionSpec('power_at_most', int(power['maximum']))
    if keyword := re.fullmatch(r'this turn except by creatures with (haste|defender)', tail, re.I):
        return FixedBlockerRestrictionSpec('keyword', keyword[1].casefold(), True, schema_version=2)
    return None


def fixed_blocker_restriction_template(text: str, *, card_name: str = '', allow_source: bool = False):
    match = re.fullmatch(r"(?P<subject>.+?) can't be blocked (?P<tail>.+)\.",
        ' '.join(text.strip().split()), re.I)
    if match is None or (spec := _blocker_spec(match['tail'])) is None:
        return None
    subject = match['subject']
    if target := direct_permanent_target_spec(subject):
        schema = target.to_target_schema()
        if 'creature' in schema.get('types_all', ()) or schema.get('types_any') == ['creature']:
            return FixedBlockerRestrictionTemplate(spec, target)
    if allow_source and (
        source_self_permanent_type(subject) is not None
        or any(SourceReferenceSpec(name).matches(subject) for name in card_name.split('//') if name.strip())
    ):
        return FixedBlockerRestrictionTemplate(spec, None)
    return None
