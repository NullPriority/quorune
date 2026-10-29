"""Immutable CR607 links to actual exile incarnations, separate from durations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .continuous_effect_model import ContinuousObjectIdentity
from .linked_exile_return_model import ExiledObjectIdentity


LINKED_ABILITY_EXILE_ANNOTATION = 'linked_ability_exile_v1'


@dataclass(frozen=True, slots=True)
class LinkedAbilityExile:
    source: ContinuousObjectIdentity
    ability_pair: str
    exiled: ExiledObjectIdentity
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported linked ability exile schema')
        if not isinstance(self.source, ContinuousObjectIdentity) or not isinstance(self.exiled, ExiledObjectIdentity):
            raise ValueError('Linked abilities require source and exile incarnations')
        if type(self.ability_pair) is not str or not self.ability_pair.strip() or self.ability_pair != self.ability_pair.strip():
            raise ValueError('Linked abilities require an exact pair identity')

    def to_dict(self):
        return {'schema_version': 1, 'source': self.source.to_dict(),
                'ability_pair': self.ability_pair, 'exiled': self.exiled.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'source', 'ability_pair', 'exiled'}:
            raise ValueError('Linked ability exile has a closed schema')
        return cls(ContinuousObjectIdentity.from_dict(value['source']), value['ability_pair'],
                   ExiledObjectIdentity.from_dict(value['exiled']), value['schema_version'])


def linked_exile_objects(records, *, source, ability_pair, current_exiled):
    """Select only this pair's still-existing exile incarnations in stable order."""
    if not isinstance(source, ContinuousObjectIdentity) or type(ability_pair) is not str or not ability_pair:
        raise ValueError('Linked return selection requires exact source and pair facts')
    records, current_exiled = tuple(records), tuple(current_exiled)
    if any(not isinstance(row, LinkedAbilityExile) for row in records) or any(
        not isinstance(row, ExiledObjectIdentity) for row in current_exiled):
        raise ValueError('Linked return selection requires typed records')
    if len({row.object_id for row in current_exiled}) != len(current_exiled) or len({row.ref for row in current_exiled}) != len(current_exiled):
        raise ValueError('Current exile incarnations must be unambiguous')
    identities = set(current_exiled)
    selected = {row.exiled for row in records
                if row.source == source and row.ability_pair == ability_pair and row.exiled in identities}
    return tuple(sorted(selected, key=lambda row: (row.ref, row.object_id, row.logical_object_id)))
