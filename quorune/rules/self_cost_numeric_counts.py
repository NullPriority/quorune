from __future__ import annotations

"""Capability boundary for intrinsic counts of current numeric creature queries."""

from collections.abc import Mapping
from dataclasses import replace

from ..object_predicate import ObjectQuerySpec


CAPABILITY = 'casting.cost.self_numeric_creature_count'


def self_cost_numeric_count_dependencies(value):
    if isinstance(value, Mapping):
        own = ()
        if value.get('kind') == 'object_count' and any(
                isinstance(row, Mapping) and isinstance(row.get('query'), Mapping)
                and row['query'].get('numeric_characteristic') is not None for row in value.get('queries', ())):
            from ..self_cast_reductions import CastReductionMetric
            metric = CastReductionMetric.from_dict(value)
            if len(metric.queries) != 1:
                raise ValueError('Numeric intrinsic counts require one controlled creature query')
            query = metric.queries[0]
            if (query.scope.value != 'controller_zone' or query.query.numeric_characteristic is None
                    or query.query.numeric_characteristic.characteristic.value not in {'power','toughness'}
                    or replace(query.query, numeric_characteristic=None) != ObjectQuerySpec(zones=('battlefield',), types_all=('creature',))):
                raise ValueError('Numeric intrinsic counts require only current controlled battlefield creatures')
            own = (CAPABILITY,)
        return tuple(sorted({*own, *(cap for child in value.values() for cap in self_cost_numeric_count_dependencies(child))}))
    if isinstance(value, (list, tuple)):
        return tuple(sorted({cap for child in value for cap in self_cost_numeric_count_dependencies(child)}))
    return ()
