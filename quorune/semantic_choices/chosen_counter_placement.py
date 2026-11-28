"""Choose one existing public counter before sending its ordinary placement intent."""

from dataclasses import dataclass

from ..counter_snapshot import permanent_counter_snapshot
from ..replacement.immutable import FrozenMap
from ..rules.chosen_counter_placement import (
    CHOSEN_COUNTER_PLACEMENT_CAPABILITY, CHOSEN_COUNTER_PLACEMENT_FIELDS,
    CHOSEN_COUNTER_PLACEMENT_OPERATION, decode_chosen_counter_placement,
)
from ..semantic_runtime.intents import PlaceCountersIntent, PlacePlayerCountersIntent
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_FIELDS = CHOSEN_COUNTER_PLACEMENT_FIELDS | {"subject_identity", "offered_counter_kinds"}
_RESPONSE_FIELDS = {"counter_kind", "action", "action_id", "a", "cap", "reason", "plan",
                    "next", "choice_schema", "label"}


def _references(effect):
    try:
        return decode_chosen_counter_placement(effect, bound=True)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _counter_choice_subject(query, ref):
    if ref is None:
        return None, ()
    if ref in query.active_seats:
        return FrozenMap({"player": ref}), tuple(sorted(query.player_counters(ref)))
    card = query.object(ref, zones=("battlefield",))
    if card is None or card.phased_out:
        return None, ()
    try:
        counters = permanent_counter_snapshot(card.counters)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    identity = FrozenMap({"object_id": card.object_id, "logical_object_id": card.logical_object_id})
    return identity, tuple(counters)


@dataclass(frozen=True, slots=True)
class ChosenCounterPlacementHandler:
    operation: str = CHOSEN_COUNTER_PLACEMENT_OPERATION
    handler_id: str = "choice.counter.chosen-placement.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 122.1", "CR 608.2b", "CR 608.2d", "CR 616.1")
    capability_dependencies: tuple[str, ...] = (CHOSEN_COUNTER_PLACEMENT_CAPABILITY,)
    continuation_fields: tuple[str, ...] = tuple(sorted(_FIELDS - {"op"}))
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "target", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = ("PlaceCountersIntent", "PlacePlayerCountersIntent")
    replay_fixture: str = "chosen-counter-placement"
    test_modules: tuple[str, ...] = ("tests.test_chosen_counter_placement",)

    def prepare(self, effect, context):
        ref, _ = _references(effect)
        if context.actor != context.stack_controller or context.actor not in context.query.active_seats:
            raise SemanticChoiceError("The original effect controller must choose the counter kind")
        identity, kinds = _counter_choice_subject(context.query, ref)
        if not kinds:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The original target has no counter to choose"))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(prompt="Choose a counter kind to add.",
                choice=ScalarChoice(field_name="counter_kind", legal_values=kinds),
                public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation, "target": ref})),
            continuation_effect=FrozenMap({**dict(effect), "subject_identity": identity, "offered_counter_kinds": kinds}),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if set(effect) != _FIELDS or set(response) - _RESPONSE_FIELDS:
            raise SemanticChoiceError("The chosen placement fields changed")
        ref, source = _references({key: effect[key] for key in CHOSEN_COUNTER_PLACEMENT_FIELDS})
        actor = continuation.semantic_frame.controller
        stack = query.stack_object(continuation.stack_ref)
        if stack is None or stack.controller != actor or actor not in query.active_seats:
            raise SemanticChoiceError("The original resolving controller must choose the counter")
        identity, kinds = _counter_choice_subject(query, ref)
        offered = effect["offered_counter_kinds"]
        kind = response.get("counter_kind")
        if (identity is None or identity != effect["subject_identity"]
                or not isinstance(offered, tuple) or not offered
                or any(type(name) is not str or not name for name in offered)
                or offered != tuple(sorted(set(offered)))
                or type(kind) is not str or kind not in offered or kind not in kinds):
            raise SemanticChoiceError("Choose a retained counter kind on the original target")
        details = dict(actor=actor, counter_name=kind, amount=1, reason="Give the target another counter", source_ref=source)
        intent = (PlacePlayerCountersIntent(player_ids=(ref,), **details) if "player" in identity
                  else PlaceCountersIntent(object_refs=(ref,), **details))
        return SemanticChoiceCompletion(intents=(intent,))
