from __future__ import annotations

"""Lower one creator-scoped token instruction through the typed interpreter."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..fixed_token_production import (
    FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID,
    FIXED_DISTRIBUTED_TOKEN_OPERATION,
    FixedTokenCreatorScope,
    fixed_token_creator_scope,
    fixed_token_effect_shape_is_closed,
)
from ..replacement.immutable import FrozenMap
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan
from .token_creation_intent import DistributedTokenCreationIntent


@dataclass(frozen=True, slots=True)
class DistributedTokenCreationHandler:
    handler_id: str = "generic.create-distributed-tokens.v1"
    schema_version: int = 1
    family: str = "effect.token-creation"
    operation: str = FIXED_DISTRIBUTED_TOKEN_OPERATION
    rule_references: tuple[str, ...] = ("101.4", "111.2", "614.16", "616.1")
    capability_dependencies: tuple[str, ...] = (FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID,)

    def lower(self, effect: Mapping[str, Any], context: ReadOnlyHandlerContext) -> IntentPlan:
        source = context.source
        if source is None:
            raise SemanticNodeError("Distributed tokens require a resolving stack object")
        actor = context.query.require_active_seat(context.actor)
        raw = dict(effect)
        selections = raw.pop("_replacement_selections", ())
        try:
            scope = fixed_token_creator_scope(raw)
            token = raw["token"]
            if raw["controller"] != actor or token.get("controller") not in {actor, "$controller"}:
                raise ValueError("Distributed token creation controller changed")
            if not fixed_token_effect_shape_is_closed({**token, "controller": "$controller"}):
                raise ValueError("Distributed token creation requires one closed token definition")
            apnap = context.query.apnap_order
            if scope is FixedTokenCreatorScope.CONTROLLER_AND_TARGET_OPPONENT:
                target = raw["target"]
                if type(target) is not str or target == actor or target not in apnap:
                    raise ValueError("Distributed token target must be a current opponent")
                creators = tuple(seat for seat in apnap if seat in {actor, target})
            else:
                creators = tuple(seat for seat in apnap
                                 if scope is FixedTokenCreatorScope.EACH_PLAYER or seat != actor)
            if not isinstance(selections, (list, tuple)):
                raise ValueError("Distributed token replacement choices must be ordered")
            intent = DistributedTokenCreationIntent(
                actor, creators, FrozenMap(token), source.stack_ref,
                source.instruction_pointer, context.default_reason, tuple(selections),
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
