"""Place the positive counter quantities captured before one zone departure."""

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.departure_counter_transfer import (
    DEPARTURE_COUNTER_TRANSFER_CAPABILITY, DEPARTURE_COUNTER_TRANSFER_OPERATION,
    departure_counter_effect, departure_counter_snapshot,
)
from ..semantic_runtime.intents import CounterPlacementAmount, PlaceCounterBatchIntent
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


@dataclass(frozen=True, slots=True)
class DepartureCounterTransferHandler:
    operation: str = DEPARTURE_COUNTER_TRANSFER_OPERATION
    handler_id: str = "choice.counter.departure-transfer.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 122.2", "CR 603.4", "CR 603.6c", "CR 608.2b", "CR 608.2h")
    capability_dependencies: tuple[str, ...] = (DEPARTURE_COUNTER_TRANSFER_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "card", "counters", "recipient_identity")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ("PlaceCounterBatchIntent", "counter_placement.place_counters")
    replay_fixture: str = "resourceful-defense"
    test_modules: tuple[str, ...] = ("tests.test_resourceful_defense",)

    def prepare(self, effect, context):
        if (set(effect) - {"recipient_identity"} != set(departure_counter_effect())
                or effect["player"] != context.actor or context.actor != context.stack_controller
                or context.actor not in context.query.active_seats):
            raise SemanticChoiceError("The original resolving trigger controls the departure transfer")
        ref = effect["card"]
        card = context.query.object(ref, zones=("battlefield",)) if isinstance(ref, str) else None
        if card is None or card.phased_out or card.controller != context.actor:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The original recipient is unavailable"))
        if "recipient_identity" in effect and effect["recipient_identity"] != card.logical_object_id:
            raise SemanticChoiceError("The original recipient changed before counter placement resumed")
        try:
            snapshot = departure_counter_snapshot(effect["counters"])
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if not snapshot:
            raise SemanticChoiceError("Counter transfer requires the original qualifying departure")
        pinned = FrozenMap({**effect, "recipient_identity": card.logical_object_id})
        placements = tuple(CounterPlacementAmount(name, amount) for name, amount in snapshot.items())
        return SemanticChoicePreparation(
            request=None, continuation_effect=pinned,
            preparation_intents=(PlaceCounterBatchIntent(
                actor=context.actor, object_ref=ref, placements=placements,
                reason=context.stack_label, source_ref=context.stack_ref,
                recipient_identity=card.logical_object_id,
            ),),
            auto_continue=AutoContinue("Place the departed permanent's counters"),
        )

    def complete(self, continuation, response, query):
        raise SemanticChoiceError("The departure counter transfer has no direct choice")
