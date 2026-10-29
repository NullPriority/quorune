"""Typed cast-cost component for one fixed printed Casualty declaration."""
from dataclasses import dataclass
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from ..casualty_model import FixedCasualtySpec, CASUALTY_CAPABILITY, CASUALTY_HANDLER
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class FixedCasualtyHandler:
    handler_id: str = CASUALTY_HANDLER
    schema_version: int = 1
    family: str = CASUALTY_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('601.2b', '601.2f', '601.2h', '601.2i', '702.153a', '702.153b', '707.10')
    capability_dependencies: tuple[str, ...] = (CASUALTY_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, 'casualty'}, field='Casualty declaration')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
                or descriptor['event'] != self.event or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Casualty declaration or complete-card boundary changed')
        try:return FixedCasualtySpec.from_dict(descriptor['casualty'])
        except (ValueError, TypeError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
