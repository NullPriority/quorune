"""Two fixed public hand-size differences, owned by the scalar result boundary."""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

UPKEEP_HAND_DIFFERENCE_CAPABILITY='quantity_expression.upkeep_hand_difference'
UPKEEP_HAND_DIFFERENCE_ORIGIN='upkeep_participant_hand_difference'


class HandDifferenceDirection(StrEnum):
    HAND_MINUS_CONSTANT='hand_minus_constant'
    CONSTANT_MINUS_HAND='constant_minus_hand'


@dataclass(frozen=True,slots=True)
class UpkeepHandDifferenceSpec:
    direction: HandDifferenceDirection
    constant: int
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.direction,HandDifferenceDirection) or type(self.constant) is not int
                or (self.direction,self.constant) not in {
                    (HandDifferenceDirection.HAND_MINUS_CONSTANT,4),
                    (HandDifferenceDirection.CONSTANT_MINUS_HAND,3)}):
            raise ValueError('Upkeep hand difference requires one closed version-one subtraction')

    def to_dict(self):
        return {'schema_version':self.schema_version,'direction':self.direction.value,'constant':self.constant}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','direction','constant'}:
            raise ValueError('Upkeep hand difference fields changed')
        return cls(HandDifferenceDirection(value['direction']),value['constant'],value['schema_version'])

    def evaluate(self,count):
        if type(count) is not int or count<0:raise ValueError('A public hand count must be a nonnegative integer')
        return max(0,count-self.constant if self.direction is HandDifferenceDirection.HAND_MINUS_CONSTANT else self.constant-count)

    def scalar_amount(self):
        from .scalar_effect_amount_model import ScalarAmountOrigin,ScalarEffectAmountSpec
        return ScalarEffectAmountSpec(ScalarAmountOrigin.UPKEEP_HAND_DIFFERENCE,schema_version=14,hand_difference=self).to_dict()
