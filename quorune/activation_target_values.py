"""Immutable values for a price based on one required creature target."""
from dataclasses import dataclass

TARGET_ACTIVATION_VALUE_CAPABILITY = 'activation.mana_cost.ability_local_target_value'


@dataclass(frozen=True, slots=True)
class TargetActivationReduction:
    target_metric: str
    generic: int = 1
    maximum_power: int | None = None

    def __post_init__(self):
        if self.target_metric not in {'power', 'color_count', 'power_at_most'}:
            raise ValueError('Unsupported target activation price metric')
        if type(self.generic) is not int or self.generic <= 0:
            raise ValueError('Target activation reduction requires positive generic mana')
        if self.target_metric == 'power_at_most':
            if type(self.maximum_power) is not int:raise ValueError('Target power threshold requires an integer')
        elif self.maximum_power is not None:
            raise ValueError('Target scalar prices reserve the power threshold')

    def to_dict(self):
        return {'schema_version':3, 'target_metric':self.target_metric,
            'generic':self.generic, 'maximum_power':self.maximum_power}

    @classmethod
    def from_dict(cls, value):
        if (set(value) != {'schema_version','target_metric','generic','maximum_power'}
                or type(value['schema_version']) is not int or value['schema_version'] != 3):
            raise ValueError('Target activation price fields are incomplete or unknown')
        return cls(value['target_metric'], value['generic'], value['maximum_power'])

    def amount(self, *, power, colors):
        if type(power) is not int:raise ValueError('Current target power must be an integer')
        if self.target_metric == 'power':return self.generic * max(0, power)
        if self.target_metric == 'power_at_most':return self.generic if power <= self.maximum_power else 0
        return self.generic * len(set(colors) & set('WUBRG'))
