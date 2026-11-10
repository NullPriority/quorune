from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..deck_copy_limits import (
    DECK_COPY_LIMIT_CAPABILITY, DECK_COPY_LIMIT_EVENT, DECK_COPY_LIMIT_HANDLER_ID, DeckCopyLimitSpec,
)


@dataclass(frozen=True, slots=True)
class DeckCopyLimitFragmentHandler:
    handler_id: str = DECK_COPY_LIMIT_HANDLER_ID
    schema_version: int = 1
    family: str = "deck.construction.copy_limit"
    event: str = DECK_COPY_LIMIT_EVENT
    rule_references: tuple[str, ...] = ("101.1", "113.6n", "903.5b")
    capability_dependencies: tuple[str, ...] = (DECK_COPY_LIMIT_CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> DeckCopyLimitSpec:
        from .ability_fragments import _fragment

        return _fragment(descriptor, handler_id=self.handler_id, event=self.event, expected_type=DeckCopyLimitSpec)

    def lower(self, descriptor: Mapping[str, Any], context: object) -> tuple[DeckCopyLimitSpec, ...]:
        del context
        return (self.validate(descriptor),)
