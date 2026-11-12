from __future__ import annotations

"""Apply the represented sacrifice after its event counter threshold."""

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.event_counter_threshold import (
    EVENT_COUNTER_THRESHOLD_CAPABILITY,
    EVENT_COUNTER_THRESHOLD_OPERATION,
    validate_event_counter_threshold,
)
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


@dataclass(frozen=True, slots=True)
class EventCounterThresholdHandler:
    operation: str = EVENT_COUNTER_THRESHOLD_OPERATION
    handler_id: str = "choice.event-counter-threshold-sacrifice.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 400.7", "CR 608.2c", "CR 608.2h", "CR 701.21a")
    capability_dependencies: tuple[str, ...] = (EVENT_COUNTER_THRESHOLD_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "source", "counter", "minimum", "observed")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("AutoContinue.prepend_effects", "sacrifice")
    replay_fixture: str = "attached-event-counter-threshold"
    test_modules: tuple[str, ...] = ("tests.test_fixed_attachment_actions",)

    def prepare(self, effect, context):
        try:
            validate_event_counter_threshold(effect, resolved=True)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if effect["player"] != context.actor or context.actor != context.stack_controller:
            raise SemanticChoiceError("Event counter threshold requires the resolving controller")
        source = (
            context.query.object(effect["source"], zones=("battlefield",))
            if effect["observed"] >= effect["minimum"] and effect["source"] is not None else None
        )
        children = ()
        if (source is not None and source.controller == context.actor and not source.phased_out
                and source.logical_object_id == context.source_logical_object_id):
            children = (FrozenMap({"op": "sacrifice", "card": source.ref}),)
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="Check the event counter total for the source sacrifice.",
                                       prepend_effects=children),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("Event counter threshold issues no player choice")
