from __future__ import annotations

from dataclasses import dataclass
from ..ability_fragments import CombatKeywordTriggerKind, CombatKeywordTriggerSpec
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class RampageFragmentHandler:
    handler_id: str = 'ability.trigger.rampage.v1'
    schema_version: int = 1
    family: str = 'ability.trigger.rampage'
    event: str = 'continuous'
    rule_references: tuple[str, ...] = ('702.23a', '702.23b', '702.23c')
    capability_dependencies: tuple[str, ...] = ('combat.trigger.rampage',)

    def validate(self, descriptor):
        from .ability_fragments import _fragment
        value = _fragment(descriptor, handler_id=self.handler_id, event=self.event,
            expected_type=CombatKeywordTriggerSpec)
        if value.kind is not CombatKeywordTriggerKind.RAMPAGE:
            raise SemanticNodeError('Rampage handler requires a Rampage fragment')
        return value

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)
