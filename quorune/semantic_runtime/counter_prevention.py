from __future__ import annotations

"""Closed self-prevention whose independent result removes one counter."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement_effects import PreventAmount, ReplacementClass, ReplacementEffect
from .component_registry import exact_fields
from .context import SemanticNodeError


COUNTER_PREVENTION_HANDLER = 'prevention.damage.fixed-self-counter.v1'
COUNTER_PREVENTION_CAPABILITY = 'damage.prevention.fixed_self_counter_removal'


@dataclass(frozen=True, slots=True)
class FixedCounterPreventionNode:
    require_counter: bool


@dataclass(frozen=True, slots=True)
class FixedCounterPreventionHandler:
    handler_id: str = COUNTER_PREVENTION_HANDLER
    schema_version: int = 1
    family: str = 'prevention.damage.fixed_self_counter'
    event: str = 'damage'
    rule_references: tuple[str, ...] = (
        '120.4b', '122.6', '400.7', '609.3', '615.1', '615.5', '615.12', '615.12a', '616.1',
    )
    capability_dependencies: tuple[str, ...] = (
        COUNTER_PREVENTION_CAPABILITY, 'counter.removal.fixed_effect',
    )

    def validate(self, descriptor: Mapping[str, Any]) -> FixedCounterPreventionNode:
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'require_counter'},
            field='fixed self-counter prevention')
        if (descriptor['handler_id'] != self.handler_id
                or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
                or descriptor['event'] != self.event or type(descriptor['require_counter']) is not bool):
            raise SemanticNodeError('Fixed counter prevention identity or predicate changed')
        return FixedCounterPreventionNode(descriptor['require_counter'])

    def lower(self, descriptor: Mapping[str, Any], context: Any) -> tuple[ReplacementEffect, ...]:
        node = self.validate(descriptor)
        identity = context.source_identity
        if not isinstance(identity, tuple) or len(identity) != 3:
            raise SemanticNodeError('Self-counter prevention requires a sealed source incarnation')
        object_id, logical_id, count = identity
        if (type(object_id) is not str or not object_id or type(logical_id) is not str or not logical_id
                or type(count) is not int or count < 0):
            raise SemanticNodeError('Counter prevention source snapshot is malformed')
        if node.require_counter and count == 0:
            return ()
        return (ReplacementEffect(
            effect_id=f'{self.handler_id}:{context.source_ref}:{context.component_id}',
            source_id=context.source_ref, event_kind=self.event,
            replacement_class=ReplacementClass.OTHER,
            conditions={'amount': {'not_in': [0]}, 'target_kind': {'eq': 'permanent'},
                'target_object_id': {'eq': object_id}, 'target_logical_object_id': {'eq': logical_id}},
            operations=(PreventAmount(),),
            label=f'{context.source_ref}: prevent damage and remove one +1/+1 counter',
        ),)
