from __future__ import annotations

"""Resolve Endure for the current or last-known permanent controller."""

from dataclasses import dataclass

from ..rules.endure_model import ENDURE_CAPABILITY, ENDURE_FIELDS, ENDURE_OPERATION, fixed_endure_amount
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import CreateTokenIntent, PlaceCountersIntent
from ..target_source import TARGETING_SOURCE_CONTEXT, TargetingSourceBinding, stack_targeting_source
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_BOUND_FIELDS = ENDURE_FIELDS | {"player", "object_ref", "logical_object_id", "reason"}


def bind_endure_instruction(host, item, effect):
    if effect.get("op") != ENDURE_OPERATION:
        return effect
    fixed_endure_amount(effect)
    source = TargetingSourceBinding.from_dict(item.context.get(TARGETING_SOURCE_CONTEXT))
    if (
        item.kind not in {"activated_ability", "triggered_ability"}
        or source.zone != "battlefield"
        or source.object_id != item.source_object_id
        or item.context.get("source_logical_object_id") not in {None, source.logical_object_id}
    ):
        raise ValueError("Endure requires its sealed source permanent incarnation")
    player = stack_targeting_source(host, item).source_controller
    if player not in host.seats:
        raise ValueError("Endure requires a known current or last-known controller")
    return {
        **effect,
        "player": player,
        "object_ref": source.object_ref,
        "logical_object_id": source.logical_object_id,
        "reason": item.label,
    }


def _bound_amount(effect, actor, query):
    if set(effect) != _BOUND_FIELDS or effect["player"] != actor or actor not in query.seats:
        raise SemanticChoiceError("Endure continuation changed its controller or fields")
    for field in ("object_ref", "logical_object_id", "reason"):
        if type(effect[field]) is not str or not effect[field]:
            raise SemanticChoiceError("Endure requires its retained object and reason")
    try:
        return fixed_endure_amount({field: effect[field] for field in ENDURE_FIELDS})
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _can_pay(effect, amount, query):
    ref = effect["object_ref"]
    row = query.object(ref, zones=("battlefield",))
    if row is None or row.phased_out or row.logical_object_id != effect["logical_object_id"]:
        return False
    if row.controller != effect["player"]:
        raise SemanticChoiceError("The enduring permanent's controller changed")
    return query.counter_placement_allowed(ref, "+1/+1", amount)


def _spirit(effect, amount):
    return CreateTokenIntent(
        actor=effect["player"], controller=effect["player"], name="Spirit Token",
        quantity=1, reason=effect["reason"], characteristics=FrozenMap({
            "type_line": "Token Creature — Spirit",
            "colors": ["W"],
            "power": str(amount),
            "toughness": str(amount),
        }),
    )


@dataclass(frozen=True, slots=True)
class SourceEndureChoiceHandler:
    operation: str = ENDURE_OPERATION
    handler_id: str = "choice.permanent.source-endure.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("111.4", "113.7a", "118.12a", "614.16", "701.63a", "701.63b")
    capability_dependencies: tuple[str, ...] = (ENDURE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "amount", "player", "object_ref", "logical_object_id", "reason")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = ("PlaceCountersIntent", "CreateTokenIntent")
    replay_fixture: str = "source-endure-controller-and-incarnation"
    test_modules: tuple[str, ...] = ("tests.test_endure",)

    def prepare(self, effect, context):
        amount = _bound_amount(effect, context.actor, context.query)
        continuation = FrozenMap(effect)
        if amount == 0 or context.actor not in context.query.active_seats:
            return SemanticChoicePreparation(
                request=None, continuation_effect=continuation,
                auto_continue=AutoContinue(reason="Endure has no result for this controller and amount."),
            )
        if not _can_pay(effect, amount, context.query):
            return SemanticChoicePreparation(
                request=None, continuation_effect=continuation,
                preparation_intents=(_spirit(effect, amount),),
                auto_continue=AutoContinue(reason="The enduring permanent cannot receive the counters."),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=f"Put {amount} +1/+1 counters on the enduring permanent or create a {amount}/{amount} Spirit Token.",
                choice=ScalarChoice(field_name="choice", legal_values=("counters", "spirit")),
                public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation}),
            ),
            continuation_effect=continuation,
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = effect.get("player")
        amount = _bound_amount(effect, actor, query)
        source = query.resolution_source
        if (
            source is None
            or source.stack_ref != continuation.stack_ref
            or source.card_ref != effect["object_ref"]
            or source.logical_object_id != effect["logical_object_id"]
        ):
            raise SemanticChoiceError("Endure choice changed its source incarnation")
        if actor not in query.active_seats or amount == 0 or not _can_pay(effect, amount, query):
            raise SemanticChoiceError("The Endure choice is no longer available")
        choice = response.get("choice")
        if set(response) - {"choice", "action", "action_id", "choice_schema"} or choice not in ("counters", "spirit"):
            raise SemanticChoiceError("Choose counters or spirit")
        if choice == "spirit":
            return SemanticChoiceCompletion(intents=(_spirit(effect, amount),))
        return SemanticChoiceCompletion(intents=(PlaceCountersIntent(
            actor=actor, object_refs=(effect["object_ref"],), counter_name="+1/+1",
            amount=amount, reason=effect["reason"], source_ref=effect["object_ref"],
            effect_generated=False,
        ),))
