"""Typed boundary for the resolving public top-card reveal."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..rules.revealed_library_top import REVEAL_TOP_CAPABILITY, REVEAL_TOP_OPERATION, RevealedLibraryTopSpec
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan, RevealEachLibraryTopIntent


@dataclass(frozen=True, slots=True)
class RevealEachLibraryTopHandler:
    handler_id: str = "generic.library.reveal-each-top.v1"
    schema_version: int = 1
    family: str = "library.reveal"
    operation: str = REVEAL_TOP_OPERATION
    rule_references: tuple[str, ...] = ("121.2c", "608.2c", "701.20a", "712.8a")
    capability_dependencies: tuple[str, ...] = (REVEAL_TOP_CAPABILITY, "zone.draw.library_to_hand")

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        if context.source is None:
            raise SemanticNodeError("Public library reveal requires a resolving stack object")
        context.query.require_active_seat(context.actor)
        try:
            spec = RevealedLibraryTopSpec.from_effect(effect)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        intent = RevealEachLibraryTopIntent(context.actor, context.default_reason, spec)
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
