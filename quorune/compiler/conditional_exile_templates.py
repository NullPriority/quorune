from __future__ import annotations

"""Source-spanned recognition for CR610.3, separate from linked triggers.

Recognition returns typed compiler data. It is not routed into executable IR
until the conditional zone-change and continuation owner is complete.
"""

from dataclasses import dataclass
import re

from ..replacement.immutable import FrozenMap
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .direct_target import direct_permanent_target_spec
from .fixed_homogeneous_target_sets import _singularize_subject


_UNTIL = re.compile(r'^Exile (?P<subject>.+?) until (?P<source>.+?) leaves the battlefield\.?$', re.IGNORECASE)
_CARDINALITY = re.compile(r'(?P<count>up to (?:one|two|three|four|five|six)|two|three|four|five|six) (?P<other>other )?target (?P<quality>.+)', re.IGNORECASE)
_COUNTS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}


@dataclass(frozen=True, slots=True)
class ConditionalSourceExileTemplate:
    target_schema: FrozenMap

    def __post_init__(self) -> None:
        if not isinstance(self.target_schema, FrozenMap):
            raise ValueError('Conditional exile target data must be immutable')


def conditional_source_exile_template(text: str, *, card_name: str,
                                     source_is_permanent: bool) -> ConditionalSourceExileTemplate | None:
    if source_is_permanent is not True:
        return None
    match = _UNTIL.fullmatch(text.strip())
    if match is None:
        return None
    if source_self_permanent_type(match['source']) is None and not SourceReferenceSpec(card_name).matches(match['source']):
        return None
    subject = match['subject']
    cardinality = _CARDINALITY.fullmatch(subject)
    minimum = maximum = 1
    if cardinality:
        count = cardinality['count'].casefold()
        maximum = _COUNTS[count.removeprefix('up to ')]
        minimum = 0 if count.startswith('up to ') else maximum
        subject = ('another target ' if cardinality['other'] else 'target ') + _singularize_subject(cardinality['quality'])
    target = direct_permanent_target_spec(subject)
    if target is None:
        return None
    schema = target.to_target_schema()
    schema.pop('count', None)
    schema['up_to' if minimum == 0 else 'count'] = maximum
    return ConditionalSourceExileTemplate(FrozenMap(schema))
