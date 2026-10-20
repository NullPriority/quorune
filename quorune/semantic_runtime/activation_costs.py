from __future__ import annotations

"""Closed activation-cost descriptors for the existing cost registry."""

from dataclasses import dataclass

from ..activation_cost_modifiers import (
    ACTIVATION_GENERIC_COST_CAPABILITY, ACTIVATION_GENERIC_COST_EVENT,
    ACTIVATION_GENERIC_COST_HANDLER, FixedActivationCostModifierSpec,
)
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedActivationCostModifierHandler:
    handler_id: str = ACTIVATION_GENERIC_COST_HANDLER
    schema_version: int = 1
    family: str = 'activation.cost.modifier.fixed_generic'
    event: str = ACTIVATION_GENERIC_COST_EVENT
    rule_references: tuple[str, ...] = ('118.7', '118.7a', '601.2f', '602.2b', '611.3a', '611.3b')
    capability_dependencies: tuple[str, ...] = (ACTIVATION_GENERIC_COST_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'modifier'},
            field='fixed activation-cost modifier')
        if (descriptor['handler_id'] != self.handler_id
                or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
                or descriptor['event'] != self.event):
            raise SemanticNodeError('Activation-cost modifier header is invalid')
        try:
            return FixedActivationCostModifierSpec.from_dict(descriptor['modifier'])
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)
