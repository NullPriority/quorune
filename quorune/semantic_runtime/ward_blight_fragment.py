"""Typed static ability fragment for the fixed Ward Blight price."""

from dataclasses import dataclass
from typing import Any, Mapping

from .ability_fragments import SemanticNodeError, WardNonmanaAbilityFragmentHandler, _fragment
from ..trigger_participation import WardSpec


WARD_BLIGHT_FRAGMENT_HANDLER_ID = "ability.trigger.ward.fixed-blight.v1"


@dataclass(frozen=True, slots=True)
class WardBlightAbilityFragmentHandler(WardNonmanaAbilityFragmentHandler):
    handler_id: str = WARD_BLIGHT_FRAGMENT_HANDLER_ID
    rule_references: tuple[str, ...] = ("118.4", "118.5", "701.68a", "702.21a")
    capability_dependencies: tuple[str, ...] = ("trigger.keyword.ward.fixed_blight", "keyword_action.blight.fixed")

    def validate(self, descriptor: Mapping[str, Any]) -> WardSpec:
        spec = _fragment(descriptor, handler_id=self.handler_id, event=self.event, expected_type=WardSpec)
        if spec.blight_amount is None:
            raise SemanticNodeError("Fixed-Blight Ward requires its typed Blight count")
        return spec
