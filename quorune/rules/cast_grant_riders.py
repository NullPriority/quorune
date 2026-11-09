from __future__ import annotations

"""Incarnation-bound ordinary replacements created by a chosen casting grant."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement import ReplacementClass, ReplacementEffect, SetField

CAST_GRANT_RIDER_FIELD = 'cast_grant_rider'
_RIDERS = frozenset({'graveyard_exile', 'tapped_entry'})


@dataclass(frozen=True, slots=True)
class CastGrantRiderBinding:
    rider: str
    logical_object_id: str
    caster: str
    source_ref: str
    owner: str | None = None

    def __post_init__(self):
        if self.rider not in _RIDERS or any(type(v) is not str or not v for v in (self.logical_object_id, self.caster, self.source_ref)):
            raise ValueError('Cast follow-up requires a closed result and spell/source identities')
        if self.owner is not None and (self.rider != 'graveyard_exile' or type(self.owner) is not str or not self.owner):
            raise ValueError('A separately bound owner requires a graveyard exile rider')

    def to_dict(self):
        return {'schema_version': 2 if self.owner is not None else 1, 'rider': self.rider,
            'logical_object_id': self.logical_object_id, 'caster': self.caster, 'source_ref': self.source_ref,
            **({'owner': self.owner} if self.owner is not None else {})}

    @classmethod
    def from_dict(cls, value):
        fields = {'schema_version', 'rider', 'logical_object_id', 'caster', 'source_ref'}
        version = value.get('schema_version') if isinstance(value, Mapping) else None
        if (type(version) is not int or version not in (1, 2)
                or set(value) != (fields | {'owner'} if version == 2 else fields)
                or version == 2 and value['owner'] is None):
            raise ValueError('Cast follow-up binding fields are closed')
        return cls(value['rider'], value['logical_object_id'], value['caster'], value['source_ref'], value.get('owner'))


def cast_grant_rider_replacements(cards, subjects):
    effects = []
    for subject in subjects:
        card = cards.get(subject.object_id)
        value = card.annotations.get(CAST_GRANT_RIDER_FIELD) if card is not None else None
        if value is None:
            continue
        binding = CastGrantRiderBinding.from_dict(value)
        owner = binding.owner or binding.caster
        if subject.origin != 'stack' or subject.logical_object_id != binding.logical_object_id or subject.owner != owner:
            continue
        conditions = {'origin': {'eq': 'stack'}, 'object_ref': {'eq': subject.object_ref},
            'logical_object_id': {'eq': subject.logical_object_id}}
        if binding.rider == 'graveyard_exile' and subject.destination == 'graveyard':
            conditions.update(destination={'eq': 'graveyard'}, owner={'eq': owner})
            operation = SetField('destination', 'exile')
            label = ('exile this spell instead of putting it in its owner\'s graveyard' if binding.owner is not None
                     else 'exile this granted spell instead of putting it in your graveyard')
        elif binding.rider == 'tapped_entry' and subject.destination == 'battlefield':
            conditions.update(destination={'eq': 'battlefield'})
            operation = SetField('tapped', True)
            label = 'this granted artifact enters tapped'
        else:
            continue
        effects.append(ReplacementEffect(effect_id='cast-grant:' + binding.rider + ':' + binding.logical_object_id,
            source_id=binding.source_ref, event_kind='zone.change', replacement_class=ReplacementClass.OTHER,
            conditions=conditions, operations=(operation,), label=binding.source_ref + ': ' + label))
    return tuple(effects)
