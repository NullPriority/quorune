"""Choose one nontarget allocation for the original upkeep source."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..counter_snapshot import permanent_counter_snapshot
from ..replacement.immutable import FrozenMap
from ..rules.source_counter_distribution import (
    SOURCE_COUNTER_DISTRIBUTION_CAPABILITY, SOURCE_COUNTER_DISTRIBUTION_OPERATION,
)
from ..semantic_runtime.counter_movement_intent import CounterMovementShare, DistributeSourceCountersIntent
from .model import (
    AutoContinue, DistributionChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_ALLOCATION = "counter_allocation"
_COUNTER = "+1/+1"
_INSTRUCTION_FIELDS = {"op", "schema_version", "player", "source"}
_OFFER_FIELDS = {"source_identity", "available", "recipients"}
_RESPONSE_FIELDS = {_ALLOCATION, "action", "action_id", "a", "cap", "reason", "plan", "next", "choice_schema", "label"}


def _validate_instruction(effect, actor, query):
    source = effect.get("source")
    version = effect.get("schema_version")
    fields = _INSTRUCTION_FIELDS | {"target"} if version == 2 else _INSTRUCTION_FIELDS
    if (set(effect) != fields or effect.get("op") != SOURCE_COUNTER_DISTRIBUTION_OPERATION
            or type(version) is not int or version not in (1, 2)
            or effect.get("player") != actor or actor not in query.active_seats
            or source is not None and (type(source) is not str or not source or source.startswith("$"))):
        raise SemanticChoiceError("Counter distribution belongs to its original controller and source")
    if version == 2:
        target = effect["target"]
        if type(target) is not str or not target or target.startswith("$") or target == source:
            raise SemanticChoiceError("Counter transfer requires its original distinct creature target")


def _live_offer(query, ref, identity, *, target=None):
    source = query.object(ref, zones=("battlefield",))
    if source is None or source.phased_out or source.logical_object_id != identity:
        return 0, {}
    try:
        available = permanent_counter_snapshot(source.counters).get(_COUNTER, 0)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if not available:
        return 0, {}
    recipients = {
        row.ref: row.logical_object_id
        for row in query.objects(zones=("battlefield",))
        if row.object_id != source.object_id and "creature" in row.types
        and (target is None or target == row.ref)
        and query.counter_placement_allowed(row.ref, _COUNTER, 1)
    }
    return available, recipients


@dataclass(frozen=True, slots=True)
class SourceCounterDistributionHandler:
    operation: str = SOURCE_COUNTER_DISTRIBUTION_OPERATION
    handler_id: str = "choice.counter.source-upkeep-distribution.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 122.5", "CR 603.3a", "CR 608.2d", "CR 608.2f")
    capability_dependencies: tuple[str, ...] = (SOURCE_COUNTER_DISTRIBUTION_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "source", "source_identity", "available", "recipients", "target")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema", "source")
    mutation_path: tuple[str, ...] = ("DistributeSourceCountersIntent", "rules.counter_movement.execute_counter_movement")
    replay_fixture: str = "source-counter-distribution"
    test_modules: tuple[str, ...] = ("tests.test_source_counter_distribution",)

    def prepare(self, effect, context):
        _validate_instruction(effect, context.actor, context.query)
        source = effect["source"]
        identity = context.source_logical_object_id
        target = effect.get("target")
        available, recipients = _live_offer(context.query, source, identity, target=target) if source is not None else (0, {})
        if not recipients:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The original source cannot move any +1/+1 counters"),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=("Choose how many +1/+1 counters to move to the target creature." if target is not None
                        else "Choose how many +1/+1 counters to move to each other creature."),
                choice=DistributionChoice(_ALLOCATION, tuple(sorted(recipients)), minimum_each=1, maximum_total=available),
                public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation, "source": source}),
            ),
            continuation_effect=FrozenMap({
                **effect, "source_identity": identity, "available": available, "recipients": recipients,
            }),
        )

    def complete(self, continuation, response, query):
        sealed = continuation.effect
        actor = continuation.semantic_frame.controller
        instruction = {key: value for key, value in sealed.items() if key not in _OFFER_FIELDS}
        _validate_instruction(instruction, actor, query)
        stack = query.stack_object(continuation.stack_ref)
        recipients = sealed.get("recipients")
        allocation = response.get(_ALLOCATION)
        if (stack is None or stack.controller != actor or instruction["source"] is None
                or type(sealed.get("source_identity")) is not str or not sealed["source_identity"]
                or type(sealed.get("available")) is not int or sealed["available"] < 1
                or not isinstance(recipients, Mapping) or not recipients
                or any(type(ref) is not str or not ref or type(identity) is not str or not identity
                       for ref, identity in recipients.items())
                or set(response) - _RESPONSE_FIELDS or not isinstance(allocation, Mapping)
                or any(ref not in recipients or type(amount) is not int or amount < 1
                       for ref, amount in allocation.items())
                or sum(allocation.values()) > sealed["available"]):
            raise SemanticChoiceError("Allocate positive amounts to original recipients within the available counter total")
        if not allocation:
            return SemanticChoiceCompletion()
        source = instruction["source"]
        identity = sealed["source_identity"]
        budget, current = _live_offer(query, source, identity, target=instruction.get("target"))
        if (budget != sealed["available"]
                or any(current.get(ref) != recipients[ref] or not query.counter_placement_allowed(ref, _COUNTER, amount)
                       for ref, amount in allocation.items())):
            raise SemanticChoiceError("The donor or a chosen recipient changed before the counter move")
        return SemanticChoiceCompletion(intents=(DistributeSourceCountersIntent(
            actor=actor, from_ref=source, from_identity=identity,
            allocations=tuple(CounterMovementShare(ref, recipients[ref], allocation[ref]) for ref in sorted(allocation)),
            reason="Move existing +1/+1 counters", source_ref=continuation.stack_ref,
        ),))
