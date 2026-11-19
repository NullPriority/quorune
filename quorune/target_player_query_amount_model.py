"""A separately versioned public quantity bound to one original player target."""
from collections.abc import Mapping
from dataclasses import dataclass
from .characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope,CharacteristicQuantityReduction
from .query_effect_amount_model import PublicQueryAmountSpec,PublicQueryAmountError

TARGET_PLAYER_QUERY_AMOUNT_KIND='target_player_public_query_amount'
TARGET_PLAYER_QUERY_AMOUNT_CAPABILITY='quantity_expression.target_player_public_query'
TARGET_PLAYER_QUERY_AMOUNT_MECHANIC='target-player-public-query-amount'


@dataclass(frozen=True,slots=True)
class TargetPlayerQueryAmountSpec:
    amount: PublicQueryAmountSpec
    target_index: int=0
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or type(self.target_index) is not int or self.target_index!=0
                or not isinstance(self.amount,PublicQueryAmountSpec)):
            raise PublicQueryAmountError('Target public amount requires its explicit version and one player target')
        quantity=self.amount.quantity
        if (self.amount.schema_version!=1 or self.amount.binding_id is not None or self.amount.coefficient<=0
                or not isinstance(quantity,CharacteristicQuantitySpec)
                or quantity.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
                or quantity.query is None or quantity.query.zones!=('battlefield',)
                or quantity.exclude_source or quantity.exclude_attached_object
                or quantity.multiplier!=1 or quantity.offset!=0
                or quantity.reduction not in {CharacteristicQuantityReduction.OBJECT_COUNT,
                    CharacteristicQuantityReduction.DISTINCT_BASIC_LAND_TYPES}):
            raise PublicQueryAmountError('Target public amount accepts an independent positive public battlefield count')

    def to_dict(self):
        return {'kind':TARGET_PLAYER_QUERY_AMOUNT_KIND,'schema_version':1,
            'target_index':self.target_index,'amount':self.amount.to_dict()}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'kind','schema_version','target_index','amount'}
                or value['kind']!=TARGET_PLAYER_QUERY_AMOUNT_KIND):
            raise PublicQueryAmountError('Target public amount fields are closed')
        return cls(PublicQueryAmountSpec.from_dict(value['amount']),value['target_index'],value['schema_version'])
