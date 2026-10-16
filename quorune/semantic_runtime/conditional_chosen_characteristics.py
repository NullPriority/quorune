from __future__ import annotations

"""Gate an existing retained-designation query with a typed live condition."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..entry_designations import (
    CHOSEN_CHARACTERISTICS_CAPABILITY,
    CONDITIONAL_CHOSEN_CHARACTERISTICS_CAPABILITY,
    CONDITIONAL_CHOSEN_CHARACTERISTICS_HANDLER_ID,
)
from .chosen_characteristics import ChosenCharacteristicsHandler
from .component_registry import exact_fields
from .conditional_continuous import validate_static_public_state_condition
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class ConditionalChosenCharacteristicsHandler:
    handler_id: str = CONDITIONAL_CHOSEN_CHARACTERISTICS_HANDLER_ID
    schema_version: int = 1
    family: str = 'continuous.characteristics.conditional_chosen_designation'
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str, ...] = (
        '109.5', '205.3m', '611.3a', '611.3b', '611.3c',
        '613.1f', '613.1g', '613.4c', '614.12a', '707.2',
    )
    capability_dependencies: tuple[str, ...] = (
        CONDITIONAL_CHOSEN_CHARACTERISTICS_CAPABILITY,
        CHOSEN_CHARACTERISTICS_CAPABILITY,
        'continuous.characteristics.fixed_public_state',
    )

    def validate(self, descriptor: Mapping[str, Any]):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'source_condition', 'body'},
                     field='conditional chosen characteristics')
        if (descriptor['handler_id'] != self.handler_id
                or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version
                or descriptor['event'] != self.event):
            raise SemanticNodeError('Conditional chosen characteristic identity changed')
        condition = validate_static_public_state_condition(descriptor['source_condition'])
        body = descriptor['body']
        if not isinstance(body, Mapping):
            raise SemanticNodeError('Conditional chosen characteristics require a closed body')
        ChosenCharacteristicsHandler().validate(body)
        return condition, body

    def lower(self, descriptor: Mapping[str, Any], context):
        condition, body = self.validate(descriptor)
        if context.public_state is None:
            raise SemanticNodeError('Conditional chosen characteristics require a public snapshot')
        if not condition.matches(context.public_state):
            return ()
        return ChosenCharacteristicsHandler().lower(body, context)
