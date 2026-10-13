from __future__ import annotations

"""Immutable CR610.3 source-departure bindings and read-only return selection.

These values do not register a trigger, move an object, or grant gameplay trust.
Conditional zone changes require their canonical movement coordinator.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .continuous_effect_model import ContinuousObjectIdentity
from .linked_exile_return_model import ExiledObjectIdentity


@dataclass(frozen=True, slots=True)
class ConditionalExileBinding:
    binding_id: str
    source: ContinuousObjectIdentity
    objects: tuple[ExiledObjectIdentity, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported conditional exile binding schema')
        if type(self.binding_id) is not str or not self.binding_id:
            raise ValueError('Conditional exile binding requires an instruction identity')
        if not isinstance(self.source, ContinuousObjectIdentity):
            raise ValueError('Conditional exile requires the original source incarnation')
        if not isinstance(self.objects, tuple) or not self.objects or any(
            not isinstance(value, ExiledObjectIdentity) for value in self.objects):
            raise ValueError('Conditional exile requires actual exiled incarnations')
        if len({value.object_id for value in self.objects}) != len(self.objects):
            raise ValueError('Conditional exile objects must be distinct')

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': self.schema_version, 'binding_id': self.binding_id,
            'source': self.source.to_dict(), 'objects': [value.to_dict() for value in self.objects]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'ConditionalExileBinding':
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'binding_id', 'source', 'objects'}:
            raise ValueError('Conditional exile binding has a closed schema')
        objects = value['objects']
        if not isinstance(objects, (list, tuple)):
            raise ValueError('Conditional exile objects must be an array')
        return cls(schema_version=value['schema_version'], binding_id=value['binding_id'],
            source=ContinuousObjectIdentity.from_dict(value['source']),
            objects=tuple(ExiledObjectIdentity.from_dict(item) for item in objects))


def conditional_source_remains(source: ContinuousObjectIdentity, cards: Mapping[str, Any]) -> bool:
    """Control changes, lost abilities and phasing do not satisfy departure."""
    card = cards.get(source.object_id)
    return bool(card is not None and card.zone == 'battlefield'
        and card.logical_object_id == source.logical_object_id)


def conditional_return_objects(binding: ConditionalExileBinding, cards: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(card for identity in binding.objects for card in (cards.get(identity.object_id),)
        if identity.matches_exiled_card(card))


def conditional_departure_return_objects(
    bindings: Sequence[ConditionalExileBinding],
    departed_sources: Sequence[ContinuousObjectIdentity],
    cards: Mapping[str, Any],
) -> tuple[Any, ...]:
    """Collect one simultaneous return set for simultaneous specified events."""
    if any(not isinstance(value, ConditionalExileBinding) for value in bindings) or any(
        not isinstance(value, ContinuousObjectIdentity) for value in departed_sources):
        raise ValueError('Conditional return collection requires typed bindings and departures')
    departed = set(departed_sources)
    selected = {}
    for binding in bindings:
        if binding.source in departed:
            for card in conditional_return_objects(binding, cards):
                selected.setdefault(card.object_id, card)
    return tuple(selected.values())
