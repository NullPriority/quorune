from __future__ import annotations

"""Typed noncopiable entry subtype results committed by the effect journal."""

from dataclasses import dataclass
from typing import Mapping
from ..creature_subtypes import canonical_creature_subtype
from ..continuous_effect_model import (ContinuousEffect,ContinuousEffectDuration,ContinuousEffectOrigin,
    ContinuousObjectIdentity,ContinuousOperation,Layer)
from ..continuous_effect_state import commit_continuous_effect
from ..object_predicate import ObjectQuerySpec


@dataclass(frozen=True,slots=True,order=True)
class EntrySubtypeGrant:
    effect_id: str
    subtype: str
    sequence: int=0

    def __post_init__(self):
        if type(self.effect_id) is not str or not self.effect_id or type(self.subtype) is not str or canonical_creature_subtype(self.subtype)!=self.subtype:
            raise ValueError('Entry subtype grant requires effect identity and canonical creature subtype')
        if type(self.sequence) is not int or self.sequence<0:raise ValueError('Entry subtype sequence must be nonnegative')


def entry_subtype_grants(event):
    values=event.payload.get('entry_subtype_grants',())
    if not isinstance(values,(list,tuple)):raise ValueError('Entry subtype grants must be an array')
    result=[]
    for value in values:
        if not isinstance(value,Mapping) or set(value)!={'effect_id','subtype','sequence'}:
            raise ValueError('Entry subtype grants use closed typed fields')
        result.append(EntrySubtypeGrant(**value))
    return tuple(result)


def commit_entry_subtype_grants(host,card,grants):
    if card.zone!='battlefield':
        if grants:raise ValueError('Entry subtype grants require the resulting battlefield object')
        return ()
    effects=[]
    for grant in grants:
        if not isinstance(grant,EntrySubtypeGrant):raise ValueError('Entry subtype commit requires typed results')
        effect=ContinuousEffect(effect_id=f'entry-subtype:{grant.effect_id}:{card.logical_object_id}:{grant.sequence}',
            source_id=grant.effect_id,layer=Layer.TYPE,sublayer='4',timestamp=host._next_zone_timestamp(),
            operations=(ContinuousOperation('add_types',(grant.subtype.title(),),field='subtypes'),),
            origin=ContinuousEffectOrigin.REPLACEMENT,duration=ContinuousEffectDuration.ZONE_OBJECT,
            applies=ObjectQuerySpec(zones=('battlefield',)),
            locked_objects=(ContinuousObjectIdentity(card.object_id,card.logical_object_id),))
        commit_continuous_effect(host.state,effect);effects.append(effect)
    return tuple(effects)
