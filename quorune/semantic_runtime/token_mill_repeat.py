"""Bind the sealed repeat instruction to a resolving source and principal."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..rules.token_mill_repeat import (
    TOKEN_MILL_REPEAT_CAPABILITY, TOKEN_MILL_REPEAT_OPERATION, TokenMillRepeatIntent, TokenMillRepeatSpec,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan
from .zone_move_intent import _freeze_replacement_selections


@dataclass(frozen=True, slots=True)
class TokenMillRepeatHandler:
    handler_id: str = "generic.mill.repeat-token-on-subtype.v1"
    schema_version: int = 1
    family: str = "zone.mill"
    operation: str = TOKEN_MILL_REPEAT_OPERATION
    rule_references: tuple[str, ...] = ("117.5", "122.1e", "701.17a", "701.17b", "701.17c", "701.17d")
    capability_dependencies: tuple[str, ...] = (TOKEN_MILL_REPEAT_CAPABILITY,)

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        source = context.source
        if source is None:
            raise SemanticNodeError("A token mill repeat requires its resolving stack object")
        actor = context.query.require_active_seat(context.actor)
        raw = dict(effect)
        selections = raw.pop("_replacement_selections", ())
        try:
            spec = TokenMillRepeatSpec.from_effect(raw, actor=actor)
            if not isinstance(selections, (list, tuple)):
                raise ValueError("Mill replacement selections require an ordered sequence")
            ordered = _freeze_replacement_selections(tuple(selections), family="Token mill repeat")
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        intent = TokenMillRepeatIntent(actor, context.default_reason, spec, source.object_id,
                                      source.logical_object_id, ordered)
        return IntentPlan(self.operation, self.handler_id, (intent,))
