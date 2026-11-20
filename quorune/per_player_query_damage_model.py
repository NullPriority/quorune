"""Explicit resolution-time public quantities for one heterogeneous player batch."""
from collections.abc import Mapping
from dataclasses import dataclass
from .announced_damage import AllocatedDamageRecipient
from .characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope,CharacteristicQuantityReduction
from .fixed_damage_set_model import PlayerDamageRelation
from .query_effect_amount_model import PublicQueryAmountSpec,PublicQueryAmountError

PER_PLAYER_QUERY_DAMAGE_KIND='per_player_public_query_damage_amount'
BOUND_PER_PLAYER_QUERY_DAMAGE_KIND='bound_per_player_public_query_damage_amount'
PER_PLAYER_QUERY_DAMAGE_CAPABILITY='damage.batch.per_player_public_quantity'
PER_PLAYER_QUERY_DAMAGE_MECHANIC='per-player-public-query-damage'


@dataclass(frozen=True,slots=True)
class PerPlayerQueryDamageSpec:
    amount: PublicQueryAmountSpec
    relation: PlayerDamageRelation
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.relation,PlayerDamageRelation)
                or not isinstance(self.amount,PublicQueryAmountSpec)):
            raise PublicQueryAmountError('Per-player damage requires explicit version, relation and quantity')
        quantity=self.amount.quantity
        if (self.amount.schema_version!=1 or self.amount.binding_id is not None or self.amount.coefficient<=0
                or not isinstance(quantity,CharacteristicQuantitySpec)
                or quantity.schema_version!=1 or quantity.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
                or quantity.query is None or quantity.query.zones!=('battlefield',)
                or quantity.exclude_source or quantity.exclude_attached_object
                or quantity.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
                or quantity.multiplier!=1 or quantity.offset!=0):
            raise PublicQueryAmountError('Per-player damage accepts independent positive public battlefield object counts')

    def to_dict(self):
        return {'kind':PER_PLAYER_QUERY_DAMAGE_KIND,'schema_version':1,
            'relation':self.relation.value,'amount':self.amount.to_dict()}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','relation','amount'}
                or value['kind']!=PER_PLAYER_QUERY_DAMAGE_KIND):
            raise PublicQueryAmountError('Per-player damage quantity fields are closed')
        return cls(PublicQueryAmountSpec.from_dict(value['amount']),PlayerDamageRelation(value['relation']),value['schema_version'])


@dataclass(frozen=True,slots=True)
class BoundPerPlayerQueryDamage:
    """Sealed positive shares; zero recipients produce no damage proposal."""
    controller: str
    relation: PlayerDamageRelation
    recipients: tuple[AllocatedDamageRecipient,...]
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.controller) is not str or not self.controller
                or not isinstance(self.relation,PlayerDamageRelation)
                or not isinstance(self.recipients,tuple)
                or any(not isinstance(r,AllocatedDamageRecipient) or r.logical_object_id is not None for r in self.recipients)
                or len({r.ref for r in self.recipients})!=len(self.recipients)
                or self.relation is PlayerDamageRelation.OPPONENTS and any(r.ref==self.controller for r in self.recipients)):
            raise PublicQueryAmountError('Bound per-player damage requires distinct player shares and its locked controller')

    @property
    def total(self):return sum(r.amount for r in self.recipients)

    def to_dict(self):
        return {'kind':BOUND_PER_PLAYER_QUERY_DAMAGE_KIND,'schema_version':1,'controller':self.controller,
            'relation':self.relation.value,'recipients':[r.to_dict() for r in self.recipients]}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','controller','relation','recipients'}
                or value['kind']!=BOUND_PER_PLAYER_QUERY_DAMAGE_KIND or not isinstance(value['recipients'],(list,tuple))):
            raise PublicQueryAmountError('Bound per-player damage fields are closed')
        return cls(value['controller'],PlayerDamageRelation(value['relation']),
            tuple(AllocatedDamageRecipient.from_dict(r) for r in value['recipients']),value['schema_version'])
