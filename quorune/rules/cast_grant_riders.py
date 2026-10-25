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

    def __post_init__(self):
        if self.rider not in _RIDERS or any(type(v) is not str or not v for v in (self.logical_object_id, self.caster, self.source_ref)):
            raise ValueError('Cast follow-up requires a closed result and spell/source identities')

    def to_dict(self):
        return {'schema_version': 1, 'rider': self.rider, 'logical_object_id': self.logical_object_id,
            'caster': self.caster, 'source_ref': self.source_ref}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {'schema_version', 'rider', 'logical_object_id', 'caster', 'source_ref'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1):
            raise ValueError('Cast follow-up binding fields are closed')
        return cls(value['rider'], value['logical_object_id'], value['caster'], value['source_ref'])


def cast_grant_rider_replacements(cards, subjects):
    effects = []
    for subject in subjects:
        card = cards.get(subject.object_id)
        value = card.annotations.get(CAST_GRANT_RIDER_FIELD) if card is not None else None
        if value is None:
            continue
        binding = CastGrantRiderBinding.from_dict(value)
        if subject.origin != 'stack' or subject.logical_object_id != binding.logical_object_id or subject.owner != binding.caster:
            continue
        conditions = {'origin': {'eq': 'stack'}, 'object_ref': {'eq': subject.object_ref},
            'logical_object_id': {'eq': subject.logical_object_id}}
        if binding.rider == 'graveyard_exile' and subject.destination == 'graveyard':
            conditions.update(destination={'eq': 'graveyard'}, owner={'eq': binding.caster})
            operation = SetField('destination', 'exile')
            label = 'exile this granted spell instead of putting it in your graveyard'
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
