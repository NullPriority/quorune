"""Immutable requests across Champion's existing movement/return boundaries."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from ..champion_model import ChampionSpec
from ..continuous_effect_model import ContinuousObjectIdentity
from ..replacement.immutable import FrozenMap
from .zone_move_intent import _freeze_replacement_selections


@dataclass(frozen=True, slots=True)
class ChampionExileIntent:
    actor: str
    source: ContinuousObjectIdentity
    ability_pair: str
    spec: ChampionSpec
    subject: ContinuousObjectIdentity | None
    subject_ref: str | None
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self):
        if not isinstance(self.source, ContinuousObjectIdentity) or not isinstance(self.spec, ChampionSpec):
            raise ValueError('Champion requires typed source and predicate facts')
        if any(type(value) is not str or not value.strip() for value in (self.actor,self.ability_pair,self.reason)) or self.ability_pair != self.ability_pair.strip():
            raise ValueError('Champion requires its controller, pair and provenance')
        if (self.subject is None) != (self.subject_ref is None) or self.subject is not None and (
            not isinstance(self.subject, ContinuousObjectIdentity) or type(self.subject_ref) is not str or not self.subject_ref):
            raise ValueError('Champion requires an exact selected incarnation or decline')
        object.__setattr__(self, 'replacement_selections', _freeze_replacement_selections(
            self.replacement_selections, family='Champion exile'))


@dataclass(frozen=True, slots=True)
class ReturnLinkedAbilityExileIntent:
    actor: str
    source: ContinuousObjectIdentity
    ability_pair: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self):
        if not isinstance(self.source, ContinuousObjectIdentity) or any(
            type(value) is not str or not value.strip() for value in (self.actor,self.ability_pair,self.reason)) or self.ability_pair != self.ability_pair.strip():
            raise ValueError('Linked return requires its original source, pair and provenance')
        object.__setattr__(self, 'replacement_selections', _freeze_replacement_selections(
            self.replacement_selections, family='Champion return'))


CHAMPION_INTENT_TYPES = (ChampionExileIntent, ReturnLinkedAbilityExileIntent)


def champion_intent_identity(intent):
    common = {'actor': intent.actor, 'source': intent.source.to_dict(),
              'ability_pair': intent.ability_pair, 'reason': intent.reason}
    if isinstance(intent, ChampionExileIntent):
        return 'champion_exile', {**common, 'spec': intent.spec.to_dict(),
            'subject': intent.subject.to_dict() if intent.subject else None,
            'subject_ref': intent.subject_ref}
    if isinstance(intent, ReturnLinkedAbilityExileIntent):
        return 'champion_return', common
    raise ValueError('Champion continuation requires a typed exile or return')


def decode_champion_intent_identity(kind, value):
    common = {'actor', 'source', 'ability_pair', 'reason'}
    fields = {'champion_exile': common | {'spec', 'subject', 'subject_ref'}, 'champion_return': common}
    if kind not in fields or not isinstance(value, Mapping) or set(value) != fields[kind]:
        raise ValueError('Champion continuation fields are malformed')
    source = ContinuousObjectIdentity.from_dict(value['source'])
    if kind == 'champion_return':
        intent = ReturnLinkedAbilityExileIntent(value['actor'], source, value['ability_pair'], value['reason'])
    else:
        subject = ContinuousObjectIdentity.from_dict(value['subject']) if value['subject'] is not None else None
        intent = ChampionExileIntent(value['actor'], source, value['ability_pair'],
            ChampionSpec.from_dict(value['spec']), subject, value['subject_ref'], value['reason'])
    return champion_intent_identity(intent)[1]
