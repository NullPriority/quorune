from __future__ import annotations

"""One fixed instant/sorcery stack-copy instruction with optional new targets."""

from collections.abc import Mapping
from dataclasses import dataclass


STACK_SPELL_COPY_CAPABILITY = 'stack.copy.fixed_instant_or_sorcery'
STACK_SPELL_COPY_MECHANIC = 'fixed-stack-spell-copy'
QUALIFIED_STACK_COPY_CAPABILITY = 'stack.copy.fixed_qualified_target'
QUALIFIED_STACK_COPY_MECHANIC = 'fixed-qualified-stack-copy'


@dataclass(frozen=True, slots=True)
class FixedStackSpellCopySpec:
    controlled_only: bool = False
    schema_version: int = 1

    def __post_init__(self):
        if type(self.controlled_only) is not bool or type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Fixed spell copy requires a closed current-controller scope')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'controlled_only': self.controlled_only}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'controlled_only'}:
            raise ValueError('Fixed spell-copy declaration fields are closed')
        return cls(**dict(value))

    @property
    def effects(self):
        return ({'op': 'copy_stack_item', 'stack': '$target.0'},)

    @property
    def target_schema(self):
        return {'zones': ['stack'], 'categories': ['spell'], 'source_exclusion': True, 'count': 1,
            'types_any': ['instant', 'sorcery'], **({'controller_relation': 'you'} if self.controlled_only else {})}

    @property
    def mechanics(self):
        return (STACK_SPELL_COPY_MECHANIC, 'cr-115-targets')


@dataclass(frozen=True, slots=True)
class FixedQualifiedStackCopySpec:
    """A separate closed kind preserves the historical spell-copy declaration."""

    target_kind: str
    source_filter: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.target_kind) is not str
                or self.target_kind not in {'permanent_spell', 'activated_or_triggered_ability', 'triggered_ability'}
                or self.source_filter is not None and type(self.source_filter) is not str
                or self.source_filter not in {None, 'artifact', 'colorless'}
                or self.source_filter is not None and self.target_kind != 'activated_or_triggered_ability'):
            raise ValueError('Qualified stack copies require one represented controlled target kind')

    def to_dict(self):
        return {'kind': 'fixed_qualified_stack_copy', 'schema_version': self.schema_version,
            'target_kind': self.target_kind, 'source_filter': self.source_filter}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {'kind', 'schema_version', 'target_kind', 'source_filter'}
                or value.get('kind') != 'fixed_qualified_stack_copy'):
            raise ValueError('Qualified stack copy fields are closed')
        return cls(value['target_kind'], value['source_filter'], value['schema_version'])

    @property
    def effects(self):
        return ({'op': 'copy_stack_item', 'stack': '$target.0',
            **({'retain_targets_only': True} if self.target_kind == 'permanent_spell' else {})},)

    @property
    def target_schema(self):
        schema = {'zones': ['stack'], 'categories': ['spell' if self.target_kind == 'permanent_spell' else 'ability'],
            'source_exclusion': True, 'count': 1, 'controller_relation': 'you'}
        if self.target_kind == 'permanent_spell':
            schema['types_any'] = ['artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker']
        elif self.target_kind == 'triggered_ability':
            schema['predicate'] = 'triggered_ability'
        else:
            schema['predicate'] = ('artifact_ability_source' if self.source_filter == 'artifact' else
                'colorless_ability_source' if self.source_filter == 'colorless' else 'activated_or_triggered_ability')
        return schema

    @property
    def mechanics(self):
        return ('cr-115-targets',)


def qualified_stack_copy_specs():
    return (FixedQualifiedStackCopySpec('permanent_spell'), FixedQualifiedStackCopySpec('triggered_ability'),
        *(FixedQualifiedStackCopySpec('activated_or_triggered_ability', source) for source in (None, 'artifact', 'colorless')))
