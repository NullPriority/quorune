from __future__ import annotations

"""One public creature selection through the existing counter placement transaction."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.blight_model import BLIGHT_CAPABILITY, BLIGHT_COUNTER, BLIGHT_FIELDS, BLIGHT_OPERATION, fixed_blight_amount
from ..semantic_runtime.intents import PlaceCountersIntent
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def blight_creature_can_receive(row, actor, amount, query):
    return (
        row is not None
        and row.zone == "battlefield"
        and not row.phased_out
        and row.controller == actor
        and "creature" in row.types
        and query.counter_placement_allowed(row.ref, BLIGHT_COUNTER, amount)
    )


def blight_placement_intent(actor, ref, amount, reason, *, effect_generated):
    return PlaceCountersIntent(
        actor=actor, object_refs=(ref,), counter_name=BLIGHT_COUNTER, amount=amount,
        reason=reason, effect_generated=effect_generated,
    )


@dataclass(frozen=True, slots=True)
class FixedBlightChoiceHandler:
    operation: str = BLIGHT_OPERATION
    handler_id: str = "choice.permanent.fixed-blight.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("608.2d", "609.3", "701.68a", "701.68b")
    capability_dependencies: tuple[str, ...] = (BLIGHT_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "amount", "_candidates", "_stack_label")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_refs")
    mutation_path: tuple[str, ...] = ("PlaceCountersIntent",)
    replay_fixture: str = "fixed-blight-creature-selection"
    test_modules: tuple[str, ...] = ("tests.test_blight",)

    def prepare(self, effect, context):
        actor = context.actor
        try:
            amount = fixed_blight_amount(effect, actor=actor)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if actor not in context.query.active_seats:
            raise SemanticChoiceError("Blight requires an active instructed player")
        candidates = {
            row.ref: row.logical_object_id for row in sorted(
                context.query.objects(zones=("battlefield",), controller=actor), key=lambda row: row.ref,
            )
            if blight_creature_can_receive(row, actor, amount, context.query)
        }
        if not candidates:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="The instructed player has no creature that can receive the counters."),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=f"Choose a creature you control to receive {amount} -1/-1 counters.",
                choice=ObjectChoice(field_name="cards", legal_refs=tuple(candidates),
                    zones=("battlefield",), minimum=1, maximum=1, controller_relation="actor"),
                public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation}),
            ),
            continuation_effect=FrozenMap({**effect, "_candidates": candidates, "_stack_label": context.stack_label}),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if set(effect) != BLIGHT_FIELDS | {"_candidates", "_stack_label"}:
            raise SemanticChoiceError("Blight continuation fields changed")
        actor = effect["player"]
        try:
            amount = fixed_blight_amount({key: effect[key] for key in BLIGHT_FIELDS}, actor=actor)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        candidates = effect["_candidates"]
        label = effect["_stack_label"]
        chosen = response.get("cards")
        if (
            type(actor) is not str or actor not in query.active_seats
            or not isinstance(candidates, Mapping)
            or any(type(ref) is not str or not ref or type(identity) is not str or not identity
                   for ref, identity in candidates.items())
            or type(label) is not str or not label
            or not isinstance(chosen, (list, tuple)) or len(chosen) != 1 or type(chosen[0]) is not str
            or set(response) - {"cards", "action", "action_id", "choice_schema"}
        ):
            raise SemanticChoiceError("Blight requires exactly one offered creature")
        ref = chosen[0]
        row = query.object(ref, zones=("battlefield",))
        if not blight_creature_can_receive(row, actor, amount, query) or candidates.get(ref) != row.logical_object_id:
            raise SemanticChoiceError("The chosen Blight creature is no longer eligible")
        return SemanticChoiceCompletion(intents=(blight_placement_intent(
            actor, ref, amount, label, effect_generated=True,
        ),))
