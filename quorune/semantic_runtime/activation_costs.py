from __future__ import annotations

"""Closed activation-cost descriptors for the existing cost registry."""

from dataclasses import dataclass

from ..activation_cost_modifiers import (
    ACTIVATION_GENERIC_COST_CAPABILITY, ACTIVATION_GENERIC_COST_EVENT,
    ACTIVATION_GENERIC_COST_HANDLER, FixedActivationCostModifierSpec,
    TARGETED_ACTIVATION_COST_HANDLER, TARGETED_ACTIVATION_COST_CAPABILITY,
    PUBLIC_QUANTITY_ACTIVATION_COST_HANDLER, PUBLIC_QUANTITY_ACTIVATION_COST_CAPABILITY,
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
                or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != self.schema_version
                or descriptor['event'] != self.event):
            raise SemanticNodeError('Activation-cost modifier header is invalid')
        try:
            spec = FixedActivationCostModifierSpec.from_dict(descriptor['modifier'])
            if spec.schema_version != self.schema_version:
                raise ValueError('Activation modifier version differs from its handler')
            return spec
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class TargetedActivationCostModifierHandler(FixedActivationCostModifierHandler):
    handler_id: str = TARGETED_ACTIVATION_COST_HANDLER
    schema_version: int = 2
    capability_dependencies: tuple[str, ...] = (TARGETED_ACTIVATION_COST_CAPABILITY,)


@dataclass(frozen=True, slots=True)
class PublicQuantityActivationCostModifierHandler(FixedActivationCostModifierHandler):
    handler_id: str = PUBLIC_QUANTITY_ACTIVATION_COST_HANDLER
    schema_version: int = 3
    capability_dependencies: tuple[str, ...] = (PUBLIC_QUANTITY_ACTIVATION_COST_CAPABILITY,)

    def validate(self, descriptor):
        from ..self_cast_reductions import CastReductionMetric, CastReductionMetricKind
        spec = super(PublicQuantityActivationCostModifierHandler, self).validate(descriptor)
        metric = CastReductionMetric.from_dict(spec.to_dict()['multiplier'])
        if metric.kind is CastReductionMetricKind.SOURCE_POWER:
            return spec
        if (metric.kind is CastReductionMetricKind.OBJECT_COUNT and metric.schema_version == 2
                and all(query.scope.value == 'controller_zone' and query.query.zones == ('battlefield',)
                    for query in metric.queries)):
            return spec
        raise SemanticNodeError('Activation quantity requires current provider power or controlled battlefield counts')
