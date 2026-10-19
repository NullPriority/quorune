from __future__ import annotations

"""One conditional self effect spanning color, abilities and additive P/T."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from ..continuous_conditions import FixedPublicStateFact, FixedPublicStateConditionKind
from ..continuous_effect_model import ContinuousOperation, Layer
from .component_registry import exact_fields
from .conditional_continuous import FixedPublicStateGrantedAbilityHandler, FixedPublicStateCharacteristicsNode
from .context import SemanticNodeError
from .fixed_characteristic_settings import validate_setting_condition


CONDITIONAL_COLOR_GRANT_HANDLER = 'continuous.ability.public-state-color-grant.v1'
CONDITIONAL_COLOR_GRANT_CAPABILITY = 'continuous.ability.public_state_color_grant'


def validate_color_grant_condition(value):
    condition = validate_setting_condition(value)
    if any(leaf.kind is FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST
            and leaf.fact is FixedPublicStateFact.PERMANENT_COLOR_IS_MOST_COMMON
            for leaf in condition.leaf_conditions()):
        raise SemanticNodeError('Color grants require facts independent of current permanent colors')
    return condition


@dataclass(frozen=True, slots=True)
class ConditionalColorGrantNode:
    characteristics: FixedPublicStateCharacteristicsNode
    colors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConditionalColorGrantHandler:
    handler_id: str = CONDITIONAL_COLOR_GRANT_HANDLER
    schema_version: int = 1
    family: str = 'continuous.ability.public_state_color_grant'
    event: str = 'characteristics.evaluate'
    source_component_layer: Layer = Layer.COLOR
    rule_references: tuple[str, ...] = (
        '113.7a', '611.3a', '611.3b', '613.1e', '613.1f', '613.1g', '613.4c', '613.6', '613.7',
    )
    capability_dependencies: tuple[str, ...] = (
        CONDITIONAL_COLOR_GRANT_CAPABILITY, 'continuous.ability.fixed_public_state_grant',
    )

    def _base_descriptor(self, descriptor):
        base = {key: value for key, value in descriptor.items() if key != 'set_colors'}
        base.update(handler_id=FixedPublicStateGrantedAbilityHandler().handler_id, schema_version=2)
        return base

    def validate(self, descriptor: Mapping[str, Any]) -> ConditionalColorGrantNode:
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'source_condition',
            'target', 'modifier', 'set_colors'}, field='conditional color grant')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event):
            raise SemanticNodeError('Conditional color grant identity changed')
        colors = descriptor['set_colors']
        if type(colors) is not list or len(colors) != 1 or colors[0] not in ('W', 'U', 'B', 'R', 'G'):
            raise SemanticNodeError('Conditional color grants replace colors with one fixed color')
        validate_color_grant_condition(descriptor['source_condition'])
        node = FixedPublicStateGrantedAbilityHandler().validate(self._base_descriptor(descriptor))
        if node.target_kind != 'source':
            raise SemanticNodeError('Conditional color grants require the original source incarnation')
        return ConditionalColorGrantNode(node, tuple(colors))

    def may_affect_static_components(self, descriptor):
        self.validate(descriptor)
        return True

    def lower(self, descriptor, context):
        node = self.validate(descriptor)
        effects = FixedPublicStateGrantedAbilityHandler().lower(self._base_descriptor(descriptor), context)
        if not effects:
            return ()
        color = replace(effects[0], effect_id=f'{context.source_object_id}:{context.component_id}:5',
            layer=Layer.COLOR, sublayer='5', operations=(ContinuousOperation('set_colors', node.colors),))
        return (color, *effects)
