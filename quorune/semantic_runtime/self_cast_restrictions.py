from __future__ import annotations

"""Printed intrinsic cast restrictions through existing public conditions."""

from dataclasses import dataclass

from ..cast_timing import ControllerCombatCastWindow, OpponentUpkeepCastWindow
from ..continuous_conditions import (
    BLOCK_DECLARATION_TIMING_FACTS, FixedPublicStateConditionKind, FixedPublicStateConditionSpec, FixedPublicStateFact,
)
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError


SELF_CAST_RESTRICTION_CAPABILITY = 'casting.restriction.self_fixed_public_condition'
SELF_CAST_RESTRICTION_HANDLER = 'ability.static.self-cast-restriction.v1'
SELF_CAST_RESTRICTION_EVENT = 'cast.restriction'


def _current_public_cast_condition(condition):
    from ..public_history_query_model import opponent_life_loss_including_departed_query

    if (condition.kind is FixedPublicStateConditionKind.HISTORY_COUNT_AT_LEAST
            and condition.amount == 1 and condition.history_query == opponent_life_loss_including_departed_query()):
        return condition
    if (condition.terms or condition.counter_name is not None or condition.predicate is not None
            or condition.history_query is not None or condition.aggregate is not None):
        raise SemanticNodeError('Intrinsic public cast conditions require one independent leaf')
    if condition.kind is FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST:
        if (condition.schema_version == 4 and condition.amount == 1
                and condition.fact in BLOCK_DECLARATION_TIMING_FACTS | {FixedPublicStateFact.ANY_CREATURE_DIED_THIS_TURN}
                and condition.fact_parameter is None and condition.quantity is None):
            return condition
    elif condition.kind is FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST:
        from ..creature_subtypes import canonical_creature_subtype
        quantity = condition.quantity
        if (condition.schema_version == 2 and type(condition.amount) is int and condition.amount > 0
                and quantity is not None and quantity.schema_version == 1
                and quantity.scope.value == 'controller_zone' and quantity.counter_name is None
                and not quantity.exclude_source and not quantity.exclude_attached_object):
            subtypes = quantity.query.subtypes_all
            if (len(subtypes) == 1 and canonical_creature_subtype(subtypes[0]) == subtypes[0]
                    and quantity.query == ObjectQuerySpec(zones=('battlefield',), subtypes_all=subtypes)):
                return condition
    raise SemanticNodeError('Unrepresented intrinsic public cast condition')


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
        marked = 'intrinsic_condition_version' in descriptor
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'source_condition',
            *({'intrinsic_condition_version'} if marked else ())}, field='Intrinsic cast restriction')
        if (descriptor['handler_id'] != self.handler_id
                or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version
                or descriptor['event'] != self.event):
            raise SemanticNodeError('Intrinsic cast-restriction identity changed')
        if marked:
            version = descriptor['intrinsic_condition_version']
            if type(version) is not int or version not in (2, 3, 4, 5):
                raise SemanticNodeError('Unsupported intrinsic public cast condition version')
            if version in (4, 5):
                window = OpponentUpkeepCastWindow if version == 4 else ControllerCombatCastWindow
                try:
                    return window.from_dict(descriptor["source_condition"])
                except ValueError as exc:
                    raise SemanticNodeError(str(exc)) from exc
            try:condition = FixedPublicStateConditionSpec.from_dict(descriptor['source_condition'])
            except ValueError as exc:raise SemanticNodeError(str(exc)) from exc
            if version == 3:
                if condition != FixedPublicStateConditionSpec(FixedPublicStateConditionKind.CONTROLLER_TURN):
                    raise SemanticNodeError('Own-turn casting requires the exact controller-turn condition')
                return condition
            return _current_public_cast_condition(condition)
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
