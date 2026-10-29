"""A complete printed Bargain declaration in the canonical cast-cost registry."""
from dataclasses import dataclass
from ..bargain_model import BARGAIN_CAPABILITY, BARGAIN_HANDLER, FixedBargainSpec
from ..card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class BargainCostHandler:
    handler_id: str = BARGAIN_HANDLER
    schema_version: int = 1
    family: str = BARGAIN_CAPABILITY
    event: str = 'cast.cost'
    rule_references: tuple[str, ...] = ('601.2b', '601.2f', '601.2h', '702.166a', '702.166b', '702.166c', '702.166d')
    capability_dependencies: tuple[str, ...] = (BARGAIN_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', REQUIRES_COMPLETE_CARD_PROGRAM_FIELD, 'bargain'}, field='Bargain declaration')
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
                or descriptor['event'] != self.event or descriptor[REQUIRES_COMPLETE_CARD_PROGRAM_FIELD] is not True):
            raise SemanticNodeError('Bargain declaration or complete-card authority changed')
        try:return FixedBargainSpec.from_dict(descriptor['bargain'])
        except (ValueError, TypeError) as exc:raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)
