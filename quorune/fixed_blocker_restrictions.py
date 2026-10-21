from __future__ import annotations

"""Closed fixed blocker predicates for a resolved, target-anchored game rule."""

from dataclasses import dataclass
from typing import Mapping

from .creature_subtypes import canonical_creature_subtype
from .declaration_fragments import DeclarationObjectPredicate, DeclarationRestrictionTemplate, StatComparison

CAPABILITY = 'combat.declaration.temporary_blocker_predicate'


@dataclass(frozen=True, slots=True)
class FixedBlockerRestrictionSpec:
    kind: str
    quality: str | int
    except_matching: bool = False
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or type(self.except_matching) is not bool:
            raise ValueError('Fixed blocker restriction version and flags are closed')
        if self.kind == 'subtype':
            valid = type(self.quality) is str and canonical_creature_subtype(self.quality) == self.quality
        elif self.kind == 'artifact_or_color':
            valid = type(self.quality) is str and self.quality in ('W', 'U', 'B', 'R', 'G') and self.except_matching
        elif self.kind == 'power_at_most':
            valid = type(self.quality) is int and self.quality >= 0 and not self.except_matching
        else:
            valid = False
        if not valid:
            raise ValueError('Fixed blocker quality or polarity is unsupported')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'kind': self.kind, 'quality': self.quality,
            'except_matching': self.except_matching}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'kind', 'quality', 'except_matching'}:
            raise ValueError('Fixed blocker restriction fields are closed')
        return cls(**dict(value))

    @property
    def slug(self):
        return f'{self.kind}-{self.quality}-{int(self.except_matching)}'

    def to_restriction(self):
        if self.kind == 'subtype':
            predicate = DeclarationObjectPredicate(**{
                'subtypes_none' if self.except_matching else 'subtypes_any': (self.quality,)})
        elif self.kind == 'artifact_or_color':
            predicate = DeclarationObjectPredicate(types_none=('artifact',), colors_none=(self.quality,))
        else:
            predicate = DeclarationObjectPredicate(stat=StatComparison('power', 'le', 'fixed', self.quality))
        return DeclarationRestrictionTemplate(template_id='temporary-fixed-blocker-'+self.slug+'-v1',
            declarations=('block',), scope='source_option', subject=predicate)
