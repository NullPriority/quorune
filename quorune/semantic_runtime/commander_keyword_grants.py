"""Commander receivers reuse the closed keyword modifier and layer owners."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..object_predicate import ObjectQuerySpec
from ..rules.commander_keyword_grants import (
    COMMANDER_KEYWORD_GRANT_HANDLER, COMMANDER_PERMANENT_QUERY_CAPABILITY,
)
from .context import SemanticNodeError
from .continuous_components import FixedQueryKeywordGrantHandler, FixedQueryProtectionGrantHandler
from .component_registry import exact_fields


@dataclass(frozen=True, slots=True)
class CommanderKeywordGrantHandler(FixedQueryKeywordGrantHandler):
    handler_id: str = COMMANDER_KEYWORD_GRANT_HANDLER
    family: str = 'continuous.ability.commander_permanent_keywords'
    capability_dependencies: tuple[str, ...] = (
        'continuous.ability.fixed_query_keyword_grant', COMMANDER_PERMANENT_QUERY_CAPABILITY,
    )
    rule_references: tuple[str, ...] = ('109.5', '903.3', '903.3d', '611.3a', '611.3b', '613.1f')

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'condition', 'modifier'},
                     field='commander keyword grant')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event):
            raise SemanticNodeError('Commander keyword grant requires its version-one event envelope')
        condition = descriptor['condition']
        if not isinstance(condition, Mapping):
            raise SemanticNodeError('Commander keyword condition must be an object')
        exact_fields(condition, {'target_controller', 'predicate', 'exclude_source'}, field='commander keyword condition')
        try:
            predicate = ObjectQuerySpec.from_dict(condition['predicate'])
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if (predicate != ObjectQuerySpec(zones=('battlefield',), commander=True)
                or condition['target_controller'] not in {'source_controller', 'source_owner'}):
            raise SemanticNodeError('Commander keyword grant requires the closed designated-permanent query')
        modifier = descriptor['modifier']
        if not isinstance(modifier, Mapping):
            raise SemanticNodeError('Commander keyword modifier must be an object')
        abilities = modifier.get('add_abilities')
        legacy = (FixedQueryProtectionGrantHandler() if isinstance(abilities, list) and 'Protection' in abilities
                  else FixedQueryKeywordGrantHandler())
        # Validate every modifier and condition field through the existing owner.
        # Only the separately checked receiver is broadened by this descriptor.
        checked = legacy.validate({**descriptor, 'handler_id': legacy.handler_id,
            'schema_version': legacy.schema_version, 'condition': {**condition,
                'predicate': replace(predicate, types_all=('creature',)).to_dict()}})
        return replace(checked, predicate=predicate)
