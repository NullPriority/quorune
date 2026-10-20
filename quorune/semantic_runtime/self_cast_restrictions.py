from __future__ import annotations

"""Printed intrinsic cast restrictions through existing public conditions."""

from dataclasses import dataclass

from ..continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError


SELF_CAST_RESTRICTION_CAPABILITY = 'casting.restriction.self_fixed_public_condition'
SELF_CAST_RESTRICTION_HANDLER = 'ability.static.self-cast-restriction.v1'
SELF_CAST_RESTRICTION_EVENT = 'cast.restriction'


@dataclass(frozen=True, slots=True)
class SelfCastRestrictionHandler:
    handler_id: str = SELF_CAST_RESTRICTION_HANDLER
    schema_version: int = 1
    family: str = 'ability.static.self_cast_restriction'
    event: str = SELF_CAST_RESTRICTION_EVENT
    rule_references: tuple[str, ...] = ('109.5', '113.6e', '601.2e', '601.3')
    capability_dependencies: tuple[str, ...] = (
        SELF_CAST_RESTRICTION_CAPABILITY, 'continuous.characteristics.fixed_public_state',
    )

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'source_condition'},
            field='Intrinsic cast restriction')
        if (descriptor['handler_id'] != self.handler_id
                or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version
                or descriptor['event'] != self.event):
            raise SemanticNodeError('Intrinsic cast-restriction identity changed')
        condition = FixedPublicStateConditionSpec.from_dict(descriptor['source_condition'])
        if condition.terms or condition.amount != 1 or condition.counter_name is not None or condition.predicate is not None:
            raise SemanticNodeError('Intrinsic cast restrictions require one complete public condition')
        if condition.kind is FixedPublicStateConditionKind.CONTROLLER_SPELL_CAST_COUNT_AT_LEAST:
            if condition.quantity is not None:
                raise SemanticNodeError('Prior spell restriction cannot replace its turn fact')
        elif condition.kind is FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST:
            quantity = condition.quantity
            if (quantity is None or quantity.scope.value != 'controller_zone'
                    or quantity.query != ObjectQuerySpec(zones=('battlefield',),
                        types_all=('land',), supertypes_all=('snow',))
                    or quantity.counter_name is not None or quantity.exclude_source or quantity.exclude_attached_object):
                raise SemanticNodeError('Snow land restriction requires the complete current controller query')
        else:
            raise SemanticNodeError('Unrepresented intrinsic cast condition')
        return condition

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)
