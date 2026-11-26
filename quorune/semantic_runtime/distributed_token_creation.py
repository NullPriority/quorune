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
from ..rules.committed_quantity_followups import COMMITTED_TOKEN_CREATOR_COUNTS_FIELD
from ..rules.qualified_token_creators import uses_qualified_token_creators, resolved_qualified_token_creators
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan
from .token_creation_intent import DistributedTokenCreationIntent


def _distributed_creators(raw, actor, apnap):
    if uses_qualified_token_creators(raw):
        return resolved_qualified_token_creators(raw, apnap), ()
    if COMMITTED_TOKEN_CREATOR_COUNTS_FIELD in raw:
        counts = raw[COMMITTED_TOKEN_CREATOR_COUNTS_FIELD]
        fields = {"op", "controller", "token", COMMITTED_TOKEN_CREATOR_COUNTS_FIELD}
        if (set(raw) != fields or raw["op"] != FIXED_DISTRIBUTED_TOKEN_OPERATION
                or raw["token"]["quantity"] != 1 or not isinstance(counts, Mapping)
                or not counts or any(seat not in apnap or type(count) is not int or count <= 0
                                    for seat, count in counts.items())):
            raise ValueError("Committed token counts require current creators and positive quantities")
        creators = tuple(seat for seat in apnap if seat in counts)
        return creators, tuple(counts[seat] for seat in creators)

    scope = fixed_token_creator_scope(raw)
    if scope is FixedTokenCreatorScope.CONTROLLER_AND_TARGET_OPPONENT:
        target = raw["target"]
        if type(target) is not str or target == actor or target not in apnap:
            raise ValueError("Distributed token target must be a current opponent")
        return tuple(seat for seat in apnap if seat in {actor, target}), ()
    return tuple(seat for seat in apnap if scope is FixedTokenCreatorScope.EACH_PLAYER or seat != actor), ()


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
            token = raw["token"]
            if raw["controller"] != actor or token.get("controller") not in {actor, "$controller"}:
                raise ValueError("Distributed token creation controller changed")
            if not fixed_token_effect_shape_is_closed({**token, "controller": "$controller"}):
                raise ValueError("Distributed token creation requires one closed token definition")
            creators, quantities = _distributed_creators(raw, actor, context.query.apnap_order)
            if not isinstance(selections, (list, tuple)):
                raise ValueError("Distributed token replacement choices must be ordered")
            intent = DistributedTokenCreationIntent(
                actor, creators, FrozenMap(token), source.stack_ref,
                source.instruction_pointer, context.default_reason, tuple(selections),
                creator_quantities=quantities,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))
