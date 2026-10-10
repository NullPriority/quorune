from __future__ import annotations

"""Graft's optional, non-targeted current-counter movement."""

from dataclasses import dataclass
from typing import Mapping
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.counter_movement_intent import MoveCountersIntent
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


GRAFT_CAPABILITY = "counter.lifecycle.graft"
GRAFT_COVERAGE = "identity_pinned_graft_counter_movement"
_FIELDS = {"op", "player", "from_ref", "from_identity", "to_ref", "to_identity"}


def _validated(effect, actor, query):
    if (not isinstance(effect, Mapping) or set(effect) != _FIELDS
            or effect.get("op") != "offer_graft_counter_movement"
            or effect.get("player") != actor or actor not in query.active_seats
            or any(type(effect[k]) is not str or not effect[k] for k in _FIELDS)):
        raise SemanticChoiceError("Graft movement requires its controller and pinned objects")
    return FrozenMap(effect)


def _possible(effect, query):
    source = query.object(effect["from_ref"], zones=("battlefield",))
    recipient = query.object(effect["to_ref"], zones=("battlefield",))
    return bool(source and recipient and source.object_id != recipient.object_id
                and not source.phased_out and not recipient.phased_out
                and source.logical_object_id == effect["from_identity"]
                and recipient.logical_object_id == effect["to_identity"]
                and source.counters.get("+1/+1", 0) > 0)


@dataclass(frozen=True, slots=True)
class GraftCounterMovementHandler:
    operation: str = "offer_graft_counter_movement"
    handler_id: str = "choice.counter.graft-movement.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 122.5", "CR 603.4", "CR 608.2c", "CR 702.58a")
    capability_dependencies: tuple[str, ...] = (GRAFT_CAPABILITY,)
    continuation_fields: tuple[str, ...] = tuple(sorted(_FIELDS - {"op"}))
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = ("MoveCountersIntent", "rules.counter_movement.execute_counter_movement", "counter_state.commit_counter_changes")
    replay_fixture: str = "fixed-graft-counter-movement"
    test_modules: tuple[str, ...] = ("tests.test_graft_rules",)

    def prepare(self, effect, context):
        pinned = _validated(effect, context.actor, context.query)
        if not _possible(pinned, context.query):
            return SemanticChoicePreparation(request=None, continuation_effect=pinned,
                auto_continue=AutoContinue("Graft cannot move a counter between its original objects"))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=f"Move a +1/+1 counter onto {pinned['to_ref']}?",
                choice=ScalarChoice(field_name="choice", legal_values=("move", "decline")),
                public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation,
                    "source": pinned['from_ref'], "recipient": pinned['to_ref']})),
            continuation_effect=pinned)

    def complete(self, continuation, response, query):
        pinned = _validated(continuation.effect, continuation.effect.get("player"), query)
        if type(response.get("choice")) is not str or response.get("choice") not in {"move", "decline"}:
            raise SemanticChoiceError("Choose move or decline")
        if response["choice"] == "decline" or not _possible(pinned, query):
            return SemanticChoiceCompletion()
        return SemanticChoiceCompletion(intents=(MoveCountersIntent(
            actor=pinned["player"], from_ref=pinned["from_ref"], from_identity=pinned["from_identity"],
            to_ref=pinned["to_ref"], to_identity=pinned["to_identity"], counter_name="+1/+1", amount=1,
            reason="Graft", source_ref=continuation.stack_ref),))
