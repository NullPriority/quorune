from __future__ import annotations

"""Immutable spell information retained by a recurring first-resolution trigger."""

from collections.abc import Mapping
from dataclasses import dataclass
from .replacement.immutable import FrozenMap, thaw_value

PARADIGM_CAPABILITY = 'casting.lifecycle.paradigm'
RESOLUTION_NAME_HISTORY_CAPABILITY = 'history.spell_resolution.names'
RETAINED_CARD_COPY_CAPABILITY = 'copy.card.retained_snapshot'
PARADIGM_MECHANIC = 'paradigm'
PARADIGM_HANDLER = 'casting.lifecycle.paradigm.v1'
PARADIGM_TRIGGER_KEY = 'builtin:paradigm'
PARADIGM_CAST_PRODUCER = 'paradigm'
PARADIGM_SNAPSHOT_FIELD = 'paradigm_spell_snapshot'


@dataclass(frozen=True, slots=True)
class RetainedSpellCopySnapshot:
    oracle_id: str
    face_id: str
    characteristics: FrozenMap
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or any(type(v) is not str or not v or v != v.strip() for v in (self.oracle_id, self.face_id))
                or not isinstance(self.characteristics, Mapping)):
            raise ValueError('A retained spell copy requires exact versioned program provenance')
        values = FrozenMap(self.characteristics)
        if (any(type(values.get(key)) is not str for key in ('name', 'mana_cost', 'type_line'))
                or not values['name'] or not values['type_line']
                or values.get('_copiable_oracle_id') != self.oracle_id
                or values.get('_copiable_face_id') != self.face_id):
            raise ValueError('Retained copy characteristics disagree with their program provenance')
        object.__setattr__(self, 'characteristics', values)

    def to_dict(self):
        return {'schema_version': self.schema_version, 'oracle_id': self.oracle_id,
                'face_id': self.face_id, 'characteristics': thaw_value(self.characteristics)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
                'schema_version', 'oracle_id', 'face_id', 'characteristics'}:
            raise ValueError('Retained spell snapshot has a closed schema')
        return cls(**value)
