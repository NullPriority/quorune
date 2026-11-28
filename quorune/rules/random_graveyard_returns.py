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
EXILE_CAST_OPERATION = "exile_random_graveyard_card_for_cast"
EXILE_CAST_SCOPE = "random_own_graveyard_exile_cast"
EXILE_CAST_MECHANIC = "random-own-graveyard-exile-cast"
EXILE_CAST_CAPABILITY = "casting.resolution.random_own_graveyard_exile"
EXILE_CAST_TEMPLATE = "random-own-graveyard-exile-cast-v1"
EXILE_CAST_DEPENDENCIES = (
    EXILE_CAST_CAPABILITY, CAPABILITY, "zone.change.destination_replacement", "zone.exile.fixed_top_resolving_cast",
    "casting.permission.source_graveyard_exile",
)
_KINDS={'card':(), 'creature card':('creature',), 'instant card':('instant',),
        'sorcery card':('sorcery',), 'instant or sorcery card':('instant','sorcery')}


@dataclass(frozen=True,slots=True)
class RandomGraveyardReturnSpec:
    subject: str
    subtype: str | None=None
    schema_version: int=1
    maximum_mana_value: int | None = None

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version not in {1, 2}
                or type(self.subject) is not str or self.subject not in _KINDS):
            raise ValueError('Random graveyard return subject/version is unsupported')
        if self.exile_for_cast:
            if (self.subject != "instant or sorcery card" or self.subtype is not None
                    or type(self.maximum_mana_value) is not int or self.maximum_mana_value < 0):
                raise ValueError("Random exile must name a bounded instant or sorcery card")
        elif self.maximum_mana_value is not None:
            raise ValueError("The legacy graveyard return has no mana value bound")
        if self.subtype is not None and (self.subject!='creature card' or type(self.subtype) is not str
                or canonical_creature_subtype(self.subtype)!=self.subtype):
            raise ValueError('Random graveyard subtype must be one canonical creature type')

    @property
    def exile_for_cast(self):
        return self.schema_version == 2

    def to_dict(self):
        row = {'schema_version':self.schema_version,'subject':self.subject,'subtype':self.subtype}
        if self.exile_for_cast:
            row["maximum_mana_value"] = self.maximum_mana_value
        return row

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value, Mapping):
            raise ValueError("Random graveyard selection must be an object")
        fields = {'schema_version','subject','subtype'}
        if value.get('schema_version') == 2:
            fields.add("maximum_mana_value")
        if set(value) != fields:
            raise ValueError('Random graveyard return fields are incomplete or unknown')
        return cls(value['subject'],value['subtype'],value['schema_version'],value.get("maximum_mana_value"))

    def public_set(self):
        query=ObjectQuerySpec(zones=('graveyard',),types_any=_KINDS[self.subject],
            subtypes_all=(self.subtype,) if self.subtype else (),
            **({"mana_value_max": self.maximum_mana_value, "token": False} if self.exile_for_cast else {}))
        destination = PublicZoneDestination.EXILE if self.exile_for_cast else PublicZoneDestination.OWNER_HAND
        return PublicZoneMoveSetSpec(query,PublicZoneOrigin.GRAVEYARD,destination,
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


def random_exile_cast_instruction(player, selection):
    return {"op": EXILE_CAST_OPERATION, "scope": EXILE_CAST_SCOPE, "player": player,
            "count": 1, "selection": selection.to_dict()}


def validate_random_exile_cast_instruction(effect, *, resolved=False):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "scope", "player", "count", "selection"}
            or effect["op"] != EXILE_CAST_OPERATION or effect["scope"] != EXILE_CAST_SCOPE
            or type(effect["count"]) is not int or effect["count"] != 1):
        raise ValueError("Random graveyard exile requires its exact single-card cast instruction")
    player = effect["player"]
    if not (type(player) is str and player and not player.startswith("$") if resolved else player == "$controller"):
        raise ValueError("Random graveyard exile requires its resolving controller")
    spec = RandomGraveyardReturnSpec.from_dict(effect["selection"])
    if not spec.exile_for_cast:
        raise ValueError("Random graveyard exile requires a bounded spell-card selection")
    return spec


def random_graveyard_return_node_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    if EXILE_CAST_MECHANIC in mechanic_ids and target_schema is None and len(effects) == 1:
        try:
            validate_random_exile_cast_instruction(effects[0])
        except (KeyError, TypeError, ValueError):
            return ()
        return EXILE_CAST_DEPENDENCIES
    if MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    if set(effect)!={'op','actor','count','selection'} or effect['op']!=OPERATION or effect['actor']!='$controller':return ()
    try:
        if RandomGraveyardReturnSpec.from_dict(effect['selection']).exile_for_cast: return ()
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


def random_exile_cast_program_requirements(program):
    required = random_graveyard_return_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (EXILE_CAST_CAPABILITY not in required or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.handlers or program.event_condition is not None
            or program.cost_schema is not None or program.destination != "graveyard"):
        raise ValueError("Random graveyard exile cast requires the complete resolving spell")
    return required


def is_closed_random_exile_cast_program(program):
    try:
        required = random_exile_cast_program_requirements(program)
    except (TypeError, ValueError, KeyError):
        return False
    return set(required).issubset(program.capability_dependencies)


def random_graveyard_return_runtime_dependencies(program):
    def walk(value):
        if isinstance(value,Mapping):
            if value.get("op") == EXILE_CAST_OPERATION:
                return set(random_exile_cast_program_requirements(program))
            if value.get('op')==OPERATION:
                required=random_graveyard_return_node_capabilities(effects=(value,),target_schema=None,mechanic_ids=program.coverage)
                if not required:raise ValueError('Random graveyard descriptor lacks a complete owned subject/count')
                return set(required)
            return {cap for child in value.values() for cap in walk(child)}
        if isinstance(value,(tuple,list)):return {cap for child in value for cap in walk(child)}
        return set()
    return tuple(sorted(walk((program.effects,program.handlers))))
