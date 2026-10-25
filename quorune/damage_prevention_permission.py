from __future__ import annotations

"""Immutable authority for CR 615.12 static unpreventability."""

from collections.abc import Mapping
from dataclasses import dataclass


UNPREVENTABLE_CAPABILITY = 'damage.prevention.prohibition.static'
UNPREVENTABLE_HANDLER = 'damage.prevention.prohibition.v1'
UNPREVENTABLE_EVENT = 'damage.permission'
UNPREVENTABLE_SOURCE_FACT = 'unpreventable-damage'


@dataclass(frozen=True, slots=True)
class StaticUnpreventableDamageSpec:
    scope: str

    def __post_init__(self):
        if type(self.scope) is not str or self.scope not in {'global','self','controller_creatures_combat'}:
            raise ValueError('Unsupported static unpreventable-damage scope')

    def to_dict(self):return {'schema_version':1,'scope':self.scope}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value) != {'schema_version','scope'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1):
            raise ValueError('Malformed static unpreventable-damage rule')
        return cls(value['scope'])


@dataclass(frozen=True, slots=True)
class DamagePreventionPermissionSnapshot:
    all_damage: bool = False
    creature_combat_controllers: tuple[str,...] = ()

    def __post_init__(self):
        values = self.creature_combat_controllers
        if (type(self.all_damage) is not bool or not isinstance(values,tuple)
                or any(type(v) is not str or not v for v in values)
                or values != tuple(sorted(set(values)))):
            raise ValueError('Malformed immutable damage-prevention permission')

    def unpreventable(self,source,*,combat:bool):
        if type(combat) is not bool:raise ValueError('Damage combat status must be boolean')
        return (self.all_damage or UNPREVENTABLE_SOURCE_FACT in source.scope_facts
            or (combat and source.controller in self.creature_combat_controllers
                and 'creature' in {v.casefold() for v in source.types}))


def decode_static_unpreventability(value):
    if (not isinstance(value,Mapping) or set(value) != {'handler_id','schema_version','event','rule'}
            or value['handler_id'] != UNPREVENTABLE_HANDLER or value['event'] != UNPREVENTABLE_EVENT
            or type(value['schema_version']) is not int or value['schema_version'] != 1):
        raise ValueError('Malformed static unpreventable-damage descriptor')
    return StaticUnpreventableDamageSpec.from_dict(value['rule'])


def unpreventability_payload_dependencies(value,*,coverage=()):
    if not isinstance(value,Mapping) or value.get('handler_id') != UNPREVENTABLE_HANDLER:return ()
    decode_static_unpreventability(value)
    from .ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    if CURRENT_ABILITY_FRAGMENT_COVERAGE not in coverage:
        raise ValueError('Static unpreventability requires current source-ability scope')
    return (UNPREVENTABLE_CAPABILITY,)
