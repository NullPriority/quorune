"""Closed public count producers and sealed heterogeneous life-loss changes."""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from .characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope,CharacteristicQuantityReduction
from .query_effect_amount_model import PublicQueryAmountSpec,PublicQueryAmountError
from .creature_subtypes import canonical_creature_subtype
from .object_predicate import ObjectQuerySpec

PER_PLAYER_QUERY_LIFE_KIND='per_player_public_query_life_loss_amount'
BOUND_PER_PLAYER_QUERY_LIFE_KIND='bound_per_player_public_query_life_loss_amount'
PER_PLAYER_QUERY_LIFE_CAPABILITY='life.loss.per_player_public_quantity'
PER_PLAYER_QUERY_LIFE_MECHANIC='per-player-public-query-life-loss'
OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION=2
OPPONENT_POISON_THRESHOLD_LIFE_VERSION=3
OPPONENT_ACTUAL_TURN_LOSS_VERSION = 4


class TurnLifeLossHistory(str, Enum):
    ACTUAL_CURRENT_TURN_LOSS = 'actual_current_turn_life_lost'


def validate_per_player_life_loss_instruction(effect, relation, *, extra_fields=()):
    all_players = relation is PlayerLifeLossRelation.ALL
    required = {"op", "amount", "scope"} if all_players else {"op", "amount"}
    if (set(effect) - (required | set(extra_fields)) or not required <= set(effect)
            or effect.get("op") != ("lose_life" if all_players else "lose_life_each_opponent")
            or all_players and effect["scope"] != "all_active_players"):
        raise ValueError("Per-player life loss lost its complete matching affected-player relation")


def unbound_per_player_query_life_spec(effect):
    if not isinstance(effect, Mapping):
        raise ValueError("Per-player life loss requires a complete instruction")
    spec = PerPlayerQueryLifeLossSpec.from_dict(effect.get("amount"))
    validate_per_player_life_loss_instruction(effect, spec.relation)
    return spec


class PlayerLifeLossRelation(str,Enum):
    ALL='all_active_players'
    OPPONENTS='controller_opponents'


@dataclass(frozen=True, slots=True)
class PoisonCounterThresholdLifeLoss:
    minimum_counters: int
    life_loss: int

    def __post_init__(self):
        if (type(self.minimum_counters) is not int or self.minimum_counters < 1
                or type(self.life_loss) is not int or self.life_loss < 1):
            raise PublicQueryAmountError('Poison-qualified life loss requires positive fixed integers')

    def to_dict(self):
        return {'minimum_counters': self.minimum_counters, 'life_loss': self.life_loss}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'minimum_counters', 'life_loss'}:
            raise PublicQueryAmountError('Poison-qualified life loss has closed threshold and loss fields')
        return cls(value['minimum_counters'], value['life_loss'])


@dataclass(frozen=True,slots=True)
class PerPlayerQueryLifeLossSpec:
    amount: PublicQueryAmountSpec | PoisonCounterThresholdLifeLoss | TurnLifeLossHistory
    relation: PlayerLifeLossRelation
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version not in (
                1, OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION, OPPONENT_POISON_THRESHOLD_LIFE_VERSION,
                OPPONENT_ACTUAL_TURN_LOSS_VERSION)
                or not isinstance(self.relation,PlayerLifeLossRelation)):
            raise PublicQueryAmountError('Per-player life loss requires an explicit version and relation')
        if self.schema_version == OPPONENT_POISON_THRESHOLD_LIFE_VERSION:
            if (self.relation is not PlayerLifeLossRelation.OPPONENTS
                    or not isinstance(self.amount, PoisonCounterThresholdLifeLoss)):
                raise PublicQueryAmountError('Poison-qualified life loss applies independently to opponents')
            return
        if self.schema_version == OPPONENT_ACTUAL_TURN_LOSS_VERSION:
            if (self.relation is not PlayerLifeLossRelation.OPPONENTS
                    or self.amount is not TurnLifeLossHistory.ACTUAL_CURRENT_TURN_LOSS):
                raise PublicQueryAmountError('Turn history loss requires each opponent’s individual current loss')
            return
        if not isinstance(self.amount, PublicQueryAmountSpec):
            raise PublicQueryAmountError('Per-player life loss requires a public object count')
        quantity=self.amount.quantity
        if (self.amount.schema_version!=1 or self.amount.binding_id is not None or self.amount.coefficient<=0
                or not isinstance(quantity,CharacteristicQuantitySpec) or quantity.schema_version!=1
                or quantity.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
                or quantity.query is None or quantity.query.zones!=('battlefield',)
                or quantity.exclude_source or quantity.exclude_attached_object
                or quantity.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
                or quantity.multiplier!=1 or quantity.offset!=0):
            raise PublicQueryAmountError('Per-player life loss accepts independent positive public battlefield object counts')
        if self.schema_version == OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION:
            self._validate_subtype_absence(quantity.query)

    def _validate_subtype_absence(self, query):
        subtypes = query.subtypes_all
        if (self.relation is not PlayerLifeLossRelation.OPPONENTS or self.amount.coefficient != 1
                or len(subtypes) != 1 or canonical_creature_subtype(subtypes[0]) != subtypes[0]
                or query != ObjectQuerySpec(zones=('battlefield',), subtypes_all=subtypes)):
            raise PublicQueryAmountError('Subtype absence requires one current public creature subtype per opponent')

    def life_loss_for_quantity(self, quantity: int) -> int:
        if self.schema_version == OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION:
            return int(quantity == 0)
        if isinstance(self.amount, PoisonCounterThresholdLifeLoss):
            if type(quantity) is not int or quantity < 0:
                raise PublicQueryAmountError('Poison-qualified life loss requires an actual poison count')
            return self.amount.life_loss if quantity >= self.amount.minimum_counters else 0
        return quantity

    def to_dict(self):
        amount = self.amount.value if isinstance(self.amount, TurnLifeLossHistory) else self.amount.to_dict()
        return {'kind':PER_PLAYER_QUERY_LIFE_KIND,'schema_version':self.schema_version,
            'relation':self.relation.value,'amount':amount}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','relation','amount'}
                or value['kind']!=PER_PLAYER_QUERY_LIFE_KIND):
            raise PublicQueryAmountError('Per-player life-loss producer fields are closed')
        version = value['schema_version']
        if version == OPPONENT_ACTUAL_TURN_LOSS_VERSION:
            amount = TurnLifeLossHistory(value['amount'])
        else:
            amount_type = (PoisonCounterThresholdLifeLoss if version == OPPONENT_POISON_THRESHOLD_LIFE_VERSION
                           else PublicQueryAmountSpec)
            amount = amount_type.from_dict(value['amount'])
        return cls(amount, PlayerLifeLossRelation(value['relation']), version)
