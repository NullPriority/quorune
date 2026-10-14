from __future__ import annotations

"""Immutable public battlefield statistic aggregate value."""

from dataclasses import dataclass
from typing import Any,Mapping

from .object_predicate import ObjectQuerySpec


PUBLIC_STAT_AGGREGATE_CAPABILITY='quantity_expression.public_characteristic_aggregate'


@dataclass(frozen=True,slots=True)
class PublicCharacteristicAggregateSpec:
    characteristic: str
    reduction: str
    controller_relation: str
    query: ObjectQuerySpec
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1
                or self.characteristic not in {'power','toughness'} or self.reduction not in {'sum','maximum'}
                or self.controller_relation not in {'any','source_controller','source_opponents'}):
            raise ValueError('Public stat aggregate identity or operation is unsupported')
        query=self.query
        if (not isinstance(query,ObjectQuerySpec) or query.zones!=('battlefield',)
                or query.owner is not None or query.controller is not None or query.excluded_controllers
                or query.exclude_ref is not None or query.known_to_actor is not None or query.include_phased_out
                or (query.state_predicate is not None and (
                    query.state_predicate.entered_this_turn or query.state_predicate.enchanted is not None
                    or query.state_predicate.equipped is not None or query.state_predicate.modified is not None))):
            raise ValueError('Public stat aggregates require a fixed public battlefield query')

    def to_dict(self):
        return {'schema_version':self.schema_version,'characteristic':self.characteristic,'reduction':self.reduction,
            'controller_relation':self.controller_relation,'query':self.query.to_dict()}

    @classmethod
    def from_dict(cls,value:Mapping[str,Any]):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','characteristic','reduction','controller_relation','query'}:
            raise ValueError('Public stat aggregate fields are incomplete or unknown')
        return cls(value['characteristic'],value['reduction'],value['controller_relation'],
            ObjectQuerySpec.from_dict(value['query']),value['schema_version'])
