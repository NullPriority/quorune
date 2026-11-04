"""Closed delayed movement of the token batch created by one instruction."""
from collections.abc import Mapping
from dataclasses import dataclass

TOKEN_CLEANUP_CAPABILITY = 'token.creation.fixed_definition_cleanup'
TOKEN_CLEANUP_MECHANIC = 'ordinary-created-token-cleanup'


@dataclass(frozen=True, slots=True)
class CreatedTokenCleanupSpec:
    action: str
    timing: str
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unknown created-token cleanup version')
        if type(self.action) is not str or self.action not in {'sacrifice', 'exile'}:
            raise ValueError('Unknown created-token cleanup action')
        if type(self.timing) is not str or self.timing not in {'next_end_step', 'controller_next_end_step', 'end_of_combat'}:
            raise ValueError('Unknown created-token cleanup timing')

    def to_dict(self):
        return {'kind': 'created_token_cleanup', 'schema_version': self.schema_version,
            'action': self.action, 'timing': self.timing}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'action', 'timing'}
                or value.get('kind') != 'created_token_cleanup'):
            raise ValueError('Created-token cleanup has unknown or missing fields')
        return cls(value['action'], value['timing'], value['schema_version'])

    def condition(self):
        if self.timing == 'end_of_combat':
            return {'phase': 'combat', 'step': 'end_combat'}
        return {'phase': 'ending', 'step': 'end_step',
            **({'player': 'controller'} if self.timing == 'controller_next_end_step' else {})}
