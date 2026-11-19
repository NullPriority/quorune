from __future__ import annotations

"""Immutable authority for CR 615.12 unpreventability."""

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousEffectDuration, ContinuousEffectError
from .replacement.immutable import immutable_fingerprint


UNPREVENTABLE_CAPABILITY = 'damage.prevention.prohibition.static'
UNPREVENTABLE_HANDLER = 'damage.prevention.prohibition.v1'
UNPREVENTABLE_EVENT = 'damage.permission'
UNPREVENTABLE_SOURCE_FACT = 'unpreventable-damage'
TEMPORARY_DAMAGE_PROHIBITION_FIELD = "temporary_damage_prevention_prohibition"
TEMPORARY_DAMAGE_PROHIBITION_KIND = "temporary_damage_prevention_prohibition"


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


@dataclass(frozen=True, slots=True)
class ResolvedDamagePreventionProhibition:
    effect_id: str
    source_id: str
    timestamp: int
    duration: ContinuousEffectDuration = ContinuousEffectDuration.UNTIL_END_OF_TURN

    def __post_init__(self):
        if (
            any(type(value) is not str or not value for value in (self.effect_id, self.source_id))
            or type(self.timestamp) is not int or self.timestamp < 0
            or self.duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN
        ):
            raise ContinuousEffectError("Damage prevention prohibition requires its source and cleanup duration")

    def to_dict(self):
        return {
            "effect_kind": TEMPORARY_DAMAGE_PROHIBITION_KIND, "schema_version": 1,
            "effect_id": self.effect_id, "source_id": self.source_id, "timestamp": self.timestamp,
            "duration": self.duration.value,
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"effect_kind", "schema_version", "effect_id", "source_id", "timestamp", "duration"}
        if (
            not isinstance(value, Mapping) or set(value) != fields
            or value["effect_kind"] != TEMPORARY_DAMAGE_PROHIBITION_KIND
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
        ):
            raise ContinuousEffectError("Malformed temporary damage prevention prohibition")
        return cls(
            value["effect_id"], value["source_id"], value["timestamp"],
            ContinuousEffectDuration(value["duration"]),
        )

    @property
    def fingerprint(self):
        return immutable_fingerprint(self.to_dict())


def temporary_damage_prevention_prohibition():
    return {"op": "damage", "source": "$stack", TEMPORARY_DAMAGE_PROHIBITION_FIELD: 1}


def validate_temporary_damage_prevention_prohibition(effect, *, resolved=False):
    expected = {"op", "source", TEMPORARY_DAMAGE_PROHIBITION_FIELD}
    if resolved:
        expected.update(field for field in ("reason", "_runtime_source") if field in effect)
    source = effect.get("source")
    version = effect.get(TEMPORARY_DAMAGE_PROHIBITION_FIELD)
    if (
        set(effect) != expected or effect.get("op") != "damage"
        or type(source) is not str or not source or not resolved and source != "$stack"
        or type(version) is not int or version != 1
    ):
        raise ValueError("Temporary damage prevention prohibition requires its exact resolving source")


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
