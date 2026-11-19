"""A resolution-bound life total, distinct from cost X and live modifiers."""
from collections.abc import Mapping
from dataclasses import dataclass
from .query_effect_amount_model import PublicQueryAmountError

CURRENT_LIFE_AMOUNT_KIND = 'current_controller_life_effect_amount'
CURRENT_LIFE_AMOUNT_CAPABILITY = 'quantity_expression.current_controller_life'
CURRENT_LIFE_AMOUNT_MECHANIC = 'current-controller-life-effect-amount'


@dataclass(frozen=True, slots=True)
class CurrentLifeEffectAmountSpec:
    binding_id: str
    coefficient: int = 1
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.binding_id) is not str or not self.binding_id
                or type(self.coefficient) is not int or self.coefficient not in {-1, 1}):
            raise PublicQueryAmountError('Current life requires one explicit signed resolution binding')

    def to_dict(self):
        return {'kind': CURRENT_LIFE_AMOUNT_KIND, 'schema_version': self.schema_version,
                'binding_id': self.binding_id, 'coefficient': self.coefficient}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {
                'kind', 'schema_version', 'binding_id', 'coefficient'}
                or value.get('kind') != CURRENT_LIFE_AMOUNT_KIND):
            raise PublicQueryAmountError('Current life amount fields are closed')
        return cls(value['binding_id'], value['coefficient'], value['schema_version'])
