from __future__ import annotations

"""Resolution-only object counts; static characteristic quantities stay layer safe."""

from dataclasses import dataclass
from typing import Any, Mapping

from .object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec


LIVE_BATTLEFIELD_QUANTITY_KIND = 'live_battlefield_quantity'
LIVE_BATTLEFIELD_QUANTITY_CAPABILITY = 'quantity_expression.live_battlefield_count'


@dataclass(frozen=True, slots=True)
class LiveBattlefieldQuantitySpec:
    controller_relation: str
    query: ObjectQuerySpec
    exclude_source: bool = False
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.controller_relation) is not str
                or self.controller_relation not in {'any', 'source_controller', 'source_opponents'}
                or type(self.exclude_source) is not bool):
            raise ValueError('Live battlefield quantity identity is unsupported')
        q = self.query
        states = (None, PermanentStatePredicateSpec(attacking=True),
            PermanentStatePredicateSpec(blocking=True),
            PermanentStatePredicateSpec(counter_name='+1/+1', minimum_counter_count=1))
        if (not isinstance(q, ObjectQuerySpec) or q.zones != ('battlefield',)
                or q.controller is not None or q.owner is not None or q.excluded_controllers
                or q.exclude_ref is not None or q.known_to_actor is not None or q.include_phased_out
                or q.numeric_characteristic is not None or q.commander is not None
                or q.state_predicate not in states):
            raise ValueError('Live counts require one closed current public battlefield query')

    def to_dict(self):
        return {'kind': LIVE_BATTLEFIELD_QUANTITY_KIND, 'schema_version': self.schema_version,
            'controller_relation': self.controller_relation, 'query': self.query.to_dict(),
            'exclude_source': self.exclude_source}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        if (not isinstance(value, Mapping) or set(value) != {
                'kind', 'schema_version', 'controller_relation', 'query', 'exclude_source'}
                or value['kind'] != LIVE_BATTLEFIELD_QUANTITY_KIND):
            raise ValueError('Live battlefield quantity fields are incomplete or unknown')
        return cls(value['controller_relation'], ObjectQuerySpec.from_dict(value['query']),
            value['exclude_source'], value['schema_version'])
