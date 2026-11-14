"""Closed printed declare-attackers casting restriction component."""
from dataclasses import dataclass

from ..declare_attack_cast_model import (
    DECLARE_ATTACK_CAST_CAPABILITY, DECLARE_ATTACK_CAST_HANDLER, DeclareAttackCastSpec,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class DeclareAttackCastRestrictionHandler:
    handler_id: str = DECLARE_ATTACK_CAST_HANDLER
    schema_version: int = 1
    family: str = 'ability.static.declare_attackers_cast_restriction'
    event: str = 'cast.restriction'
    rule_references: tuple[str, ...] = ('117.1a', '508.1', '508.2', '508.4', '601.3')
    capability_dependencies: tuple[str, ...] = (DECLARE_ATTACK_CAST_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'restriction'},
            field='Declare-attackers cast restriction')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event):
            raise SemanticNodeError('Declare-attackers cast restriction identity changed')
        try:
            return DeclareAttackCastSpec.from_dict(descriptor['restriction'])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)
