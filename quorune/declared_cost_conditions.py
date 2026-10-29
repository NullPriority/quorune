"""Linked declaration predicates for ordinary conditional resolution branches."""
from collections.abc import Mapping
from dataclasses import dataclass
from .declared_cast_cost_model import DeclaredCastCostFacts, DeclaredCastCostKind, DeclaredCastCostChoice


@dataclass(frozen=True, slots=True)
class DeclaredCastCostCondition:
    cost_kind: DeclaredCastCostKind
    instance_id: str

    def __post_init__(self):
        DeclaredCastCostChoice(self.cost_kind, self.instance_id, False)

    def to_dict(self):
        return {'schema_version': 1, 'kind': 'declared_cast_cost', 'cost_kind': self.cost_kind.value, 'instance_id': self.instance_id}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'kind', 'cost_kind', 'instance_id'}:
            raise ValueError('Linked declaration condition has a closed schema')
        if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['kind'] != 'declared_cast_cost':
            raise ValueError('Linked declaration condition version is unsupported')
        return cls(DeclaredCastCostKind(value['cost_kind']), value['instance_id'])

    def matches(self, value):
        return DeclaredCastCostFacts.from_dict(value).chosen(self.cost_kind, self.instance_id)


def validate_declared_cost_instruction(effect):
    from .resolution_conditions import RESOLUTION_CONDITION_OPERATION, cast_fact_result_is_fixed
    fields = {'op', 'schema_version', 'player', 'condition', 'cast_fact', 'effects', 'mechanic_ids', 'otherwise_effects', 'otherwise_mechanic_ids'}
    if (not isinstance(effect, Mapping) or set(effect) != fields or effect['op'] != RESOLUTION_CONDITION_OPERATION
            or type(effect['schema_version']) is not int or effect['schema_version'] != 12
            or type(effect['player']) is not str or not effect['player']):
        raise ValueError('Declared-cost resolution has a closed schema')
    condition = DeclaredCastCostCondition.from_dict(effect['condition'])
    fact = effect['cast_fact']
    if isinstance(fact, Mapping):DeclaredCastCostFacts.from_dict(fact)
    elif fact != '$context.declared_cast_costs':raise ValueError('Declared-cost resolution lost its sealed facts')
    for values, ids in ((effect['effects'], effect['mechanic_ids']), (effect['otherwise_effects'], effect['otherwise_mechanic_ids'])):
        if (not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 8 or not isinstance(ids, (list, tuple)) or not ids
                or any(not isinstance(v, Mapping) or v.get('op') == RESOLUTION_CONDITION_OPERATION for v in values)
                or any(type(v) is not str or not v for v in ids) or len(set(ids)) != len(ids)
                or not cast_fact_result_is_fixed(values, ids, allow_targeted_damage_source=True)):
            raise ValueError('Declared-cost branches require bounded independently represented fixed results')
    return condition, tuple(effect['effects'])
