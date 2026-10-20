from __future__ import annotations

"""Full-current numeric Defender exceptions, evaluated only at declaration."""

from dataclasses import dataclass

from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from ..defender_permission import (
    DEFENDER_DECLARATION_CAPABILITY, DEFENDER_DECLARATION_EVENT, DEFENDER_DECLARATION_HANDLER,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class DefenderDeclarationRule:
    source_condition: FixedPublicStateConditionSpec

    def __post_init__(self):
        condition = self.source_condition
        if not isinstance(condition, FixedPublicStateConditionSpec):
            raise ValueError('Defender declaration conditions must be typed')
        if condition.kind not in {FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS,
                FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY}:
            raise ValueError('This declaration owner requires one numeric source or controlled-creature condition')
        query = condition.predicate
        if (query is None or query.numeric_characteristic is None
                or query.numeric_characteristic.characteristic.value not in {'power', 'toughness'}):
            raise ValueError('Defender declaration conditions require a current power or toughness predicate')


@dataclass(frozen=True, slots=True)
class DefenderDeclarationPermissionHandler:
    handler_id: str = DEFENDER_DECLARATION_HANDLER
    schema_version: int = 1
    family: str = 'combat.defender_permission.current_numeric'
    event: str = DEFENDER_DECLARATION_EVENT
    rule_references: tuple[str, ...] = ('609.4', '702.3b', '508.1a', '508.1c', '611.3a')
    capability_dependencies: tuple[str, ...] = (DEFENDER_DECLARATION_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'permission', 'source_condition'},
            field='declaration-time Defender permission')
        if (descriptor['handler_id'] != self.handler_id or descriptor['event'] != self.event
                or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
                or descriptor['permission'] != 'ignore_defender'):
            raise SemanticNodeError('Defender declaration permission header is invalid')
        try:
            return DefenderDeclarationRule(FixedPublicStateConditionSpec.from_dict(descriptor['source_condition']))
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)
