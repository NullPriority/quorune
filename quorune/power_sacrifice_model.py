"""Closed current-power predicate for one controlled creature sacrifice price."""
from .object_predicate import ObjectQuerySpec
from .target_numeric import TargetNumericCharacteristic, TargetNumericComparison, TargetNumericCharacteristicSpec

POWER_SACRIFICE_CAPABILITY = 'casting.additional_cost.creature_power_minimum'


def power_sacrifice_predicate(minimum):
    if type(minimum) is not int or minimum < 0:
        raise ValueError('Creature sacrifice power requires a fixed nonnegative minimum')
    return ObjectQuerySpec(zones=('battlefield',), controller='$actor', types_all=('creature',), known_to_actor=True,
        numeric_characteristic=TargetNumericCharacteristicSpec(TargetNumericCharacteristic.POWER, TargetNumericComparison.AT_LEAST, minimum))


def validate_power_sacrifice_variant(version, operation, count, predicate, unblocked_attacker, count_parameter):
    numeric = predicate.numeric_characteristic
    if version != 4:
        return
    if (numeric is None or operation != 'sacrifice_one' or count != 1 or unblocked_attacker or count_parameter is not None
            or predicate != power_sacrifice_predicate(numeric.value)):
        raise ValueError('Zone-cost schema four requires one creature with power at least its fixed minimum')
