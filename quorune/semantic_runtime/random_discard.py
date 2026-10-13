from __future__ import annotations

"""Typed random-discard preparation with one replacement-aware zone batch."""

from dataclasses import dataclass
from ..random_discard import (
    RANDOM_DISCARD_OPERATION, RANDOM_DISCARD_CAPABILITY, RandomDiscardIntent, resolve_random_discard,
)
from .context import SemanticNodeError
from .intents import IntentPlan, MoveObjectsSimultaneouslyIntent
from ..zone_trigger_events import ZoneTransitionKind


@dataclass(frozen=True, slots=True)
class RandomDiscardHandler:
    operation: str = RANDOM_DISCARD_OPERATION
    handler_id: str = "generic.discard-random-cards.v1"
    schema_version: int = 1
    family: str = RANDOM_DISCARD_CAPABILITY
    rule_references: tuple[str, ...] = ("101.4", "402.1", "608.2c", "701.9a", "701.9b", "701.9c")
    capability_dependencies: tuple[str, ...] = (RANDOM_DISCARD_CAPABILITY,)

    def lower(self, effect, context):
        if (set(effect) - {"op", "actor", "players", "count", "reason", "_replacement_selections"}
                or not {"op", "actor", "players", "count"} <= set(effect)
                or effect["op"] != self.operation or effect["actor"] != context.actor):
            raise SemanticNodeError("Random discard fields or actor are invalid")
        if context.source is None:
            raise SemanticNodeError("Random discard requires its authoritative stack instruction identity")
        raw = effect["players"]
        if raw == "all":
            players = context.query.apnap_order
        elif raw == "opponents":
            players = tuple(player for player in context.query.apnap_order if player != context.actor)
        elif isinstance(raw, (list, tuple)) and len(raw) == 1 and type(raw[0]) is str:
            players = (context.query.require_active_seat(raw[0]),)
        else:
            raise SemanticNodeError("Random discard requires a represented active player set")
        try:
            intent = RandomDiscardIntent(
                actor=context.actor, players=players, count=effect["count"],
                randomization_key=f"{context.source.stack_ref}|{context.source.instruction_pointer}",
                reason=str(effect.get("reason") or context.default_reason),
                replacement_selections=effect.get("_replacement_selections", ()),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))


def execute_random_discard(sink, intent):
    def commit(refs):
        return sink.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
            actor=intent.actor, object_refs=refs, expected_zones=("hand",),
            destination="graveyard", reason=intent.reason, transition_kind=ZoneTransitionKind.DISCARD,
            replacement_selections=intent.replacement_selections,
        ))
    return resolve_random_discard(sink, intent, commit_batch=commit)
