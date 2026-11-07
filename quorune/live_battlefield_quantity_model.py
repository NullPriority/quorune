from __future__ import annotations

"""Resolution-only object counts; static characteristic quantities stay layer safe."""

from dataclasses import dataclass
from typing import Any, Mapping

from .object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec


LIVE_BATTLEFIELD_QUANTITY_KIND = 'live_battlefield_quantity'
LIVE_BATTLEFIELD_QUANTITY_CAPABILITY = 'quantity_expression.live_battlefield_count'
LIVE_QUALIFIED_CREATURE_COUNT_CAPABILITY = 'quantity_expression.live_qualified_creature_count'


def _closed_qualified_creature_query(query: ObjectQuerySpec) -> bool:
    base = {'zones': ('battlefield',), 'types_all': ('creature',)}
    numeric = query.numeric_characteristic
    if numeric is not None:
        return (numeric.characteristic.value in ('power', 'toughness')
                and query == ObjectQuerySpec(**base, numeric_characteristic=numeric))
    state = query.state_predicate
    return (state in (PermanentStatePredicateSpec(modified=True),
                      PermanentStatePredicateSpec(minimum_total_counter_count=1))
            and query == ObjectQuerySpec(**base, state_predicate=state))


@dataclass(frozen=True, slots=True)
class LiveBattlefieldQuantitySpec:
    controller_relation: str
    query: ObjectQuerySpec
    exclude_source: bool = False
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version not in (1, 2)
                or type(self.controller_relation) is not str
                or self.controller_relation not in {'any', 'source_controller', 'source_opponents'}
                or type(self.exclude_source) is not bool):
            raise ValueError('Live battlefield quantity identity is unsupported')
        q = self.query
        if self.schema_version == 2:
            if (self.controller_relation != 'source_controller' or not isinstance(q, ObjectQuerySpec)
                    or not _closed_qualified_creature_query(q)):
                raise ValueError('Qualified live counts require one controlled creature predicate')
            return
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
