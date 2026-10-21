from __future__ import annotations

"""Typed untargeted random subsets of the resolving owner's graveyard."""

from dataclasses import dataclass
from typing import Mapping
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec
from ..public_zone_moves import (PublicZoneMoveSetSpec,PublicZoneOrigin,PublicZoneDestination,
    PublicZoneRelationAxis,PublicZoneSeatRelation,snapshot_public_zone_move_set)
from .random_selection import sample_identity_rows

OPERATION='return_random_graveyard_cards'
MECHANIC='random-own-graveyard-return'
CAPABILITY='zone.return.random_own_graveyard'
_KINDS={'card':(), 'creature card':('creature',), 'instant card':('instant',),
        'sorcery card':('sorcery',), 'instant or sorcery card':('instant','sorcery')}


@dataclass(frozen=True,slots=True)
class RandomGraveyardReturnSpec:
    subject: str
    subtype: str | None=None
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1 or self.subject not in _KINDS:
            raise ValueError('Random graveyard return subject/version is unsupported')
        if self.subtype is not None and (self.subject!='creature card' or type(self.subtype) is not str
                or canonical_creature_subtype(self.subtype)!=self.subtype):
            raise ValueError('Random graveyard subtype must be one canonical creature type')

    def to_dict(self):return {'schema_version':self.schema_version,'subject':self.subject,'subtype':self.subtype}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','subject','subtype'}:
            raise ValueError('Random graveyard return fields are incomplete or unknown')
        return cls(value['subject'],value['subtype'],value['schema_version'])

    def public_set(self):
        query=ObjectQuerySpec(zones=('graveyard',),types_any=_KINDS[self.subject],
            subtypes_all=(self.subtype,) if self.subtype else ())
        return PublicZoneMoveSetSpec(query,PublicZoneOrigin.GRAVEYARD,PublicZoneDestination.OWNER_HAND,
            relation_axis=PublicZoneRelationAxis.OWNER,seat_relation=PublicZoneSeatRelation.ACTOR,schema_version=2)


@dataclass(frozen=True,slots=True)
class RandomGraveyardReturnIntent:
    actor: str
    count: int
    selection: RandomGraveyardReturnSpec
    instruction_key: str
    reason: str
    replacement_selections: tuple=()

    def __post_init__(self):
        if (any(type(v) is not str or not v for v in (self.actor,self.instruction_key,self.reason))
                or type(self.count) is not int or self.count<0 or not isinstance(self.selection,RandomGraveyardReturnSpec)):
            raise ValueError('Random graveyard return intent requires typed count, subject and instruction identity')
        from ..semantic_runtime.zone_move_intent import _freeze_replacement_selections
        object.__setattr__(self,'replacement_selections',_freeze_replacement_selections(self.replacement_selections,family='Random graveyard return'))


def random_graveyard_return_node_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    if MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    if set(effect)!={'op','actor','count','selection'} or effect['op']!=OPERATION or effect['actor']!='$controller':return ()
    try:RandomGraveyardReturnSpec.from_dict(effect['selection'])
    except (TypeError,ValueError):return ()
    if type(effect['count']) is int:return (CAPABILITY,) if effect['count']>=0 else ()
    from ..query_effect_amount_model import CastXAmountSpec,DECLARED_AMOUNT_CAPABILITY
    try:spec=CastXAmountSpec.from_dict(effect['count'])
    except (TypeError,ValueError):return ()
    return tuple(sorted((CAPABILITY,DECLARED_AMOUNT_CAPABILITY))) if spec.coefficient==1 and 'declared-effect-amount' in mechanic_ids else ()


def resolve_random_graveyard_return(host,intent,*,commit_batch):
    snapshot=snapshot_public_zone_move_set(host,actor=intent.actor,spec=intent.selection.public_set())
    rows=tuple({'object_id':obj.object_id,'ref':obj.ref} for obj in snapshot.objects)
    selected=sample_identity_rows(rows,intent.count,seed=host.state.config.seed,
        instruction_key=intent.instruction_key,namespace='random-graveyard-return',seat=intent.actor)
    return commit_batch(tuple(row['ref'] for row in selected)) if selected else ()


def random_graveyard_return_runtime_dependencies(program):
    def walk(value):
        if isinstance(value,Mapping):
            if value.get('op')==OPERATION:
                required=random_graveyard_return_node_capabilities(effects=(value,),target_schema=None,mechanic_ids=program.coverage)
                if not required:raise ValueError('Random graveyard descriptor lacks a complete owned subject/count')
                return set(required)
            return {cap for child in value.values() for cap in walk(child)}
        if isinstance(value,(tuple,list)):return {cap for child in value for cap in walk(child)}
        return set()
    return tuple(sorted(walk((program.effects,program.handlers))))
