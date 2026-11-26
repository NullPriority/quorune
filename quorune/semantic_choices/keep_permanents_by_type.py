"""Collect each public type choice before the existing APNAP movement commit."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.keep_permanents_by_type import (
    KEEP_PERMANENTS_BY_TYPE_CAPABILITY, KEEP_PERMANENTS_BY_TYPE_OPERATION,
    KeepPermanentsByType, keep_by_type_plan, unkept_type_choice_refs, validate_keep_by_type_choices,
)
from .apnap_commit import APNAP_OBJECT_COMMIT_OPERATION
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


_ROOT_FIELDS = {"op", "schema_version", "mode", "player"}
_STAGE_FIELDS = _ROOT_FIELDS | {"controller", "order", "plan", "chosen"}


def _choice_state(effect, controller, query):
    if (set(effect) not in (_ROOT_FIELDS, _STAGE_FIELDS) or effect["op"] != KEEP_PERMANENTS_BY_TYPE_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or controller not in query.active_seats):
        raise SemanticChoiceError("The keep-by-type choice lost its original resolving controller")
    order = tuple(query.apnap_order)
    if "plan" in effect:
        if (effect["controller"] != controller or not isinstance(effect["order"], (list, tuple))
                or tuple(effect["order"]) != order):
            raise SemanticChoiceError("The keep-by-type continuation changed its controller or order")
    elif effect["player"] != controller:
        raise SemanticChoiceError("Keep-by-type choice must start with its resolving controller")
    try:
        scope = KeepPermanentsByType(effect["mode"])
        plan = keep_by_type_plan(scope, query, order)
        if "plan" in effect and (not isinstance(effect["plan"], Mapping) or FrozenMap(effect["plan"]) != plan):
            raise ValueError("The public permanent domains changed during the type choices")
        chosen = effect.get("chosen", ())
        validate_keep_by_type_choices(plan, chosen)
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return scope, plan, tuple(chosen), order


def _commit_effects(plan, chosen, controller):
    try:
        refs = unkept_type_choice_refs(plan, chosen)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if not refs:
        return ()
    return (FrozenMap({
        "op": APNAP_OBJECT_COMMIT_OPERATION, "actor": controller, "object_refs": refs,
        "expected_zones": ["battlefield"], "destination": "graveyard",
        "reason": "simultaneous keep-by-type sacrifice", "event_code": "choice.sacrifice",
        "message": "Sacrificed the unchosen eligible permanents.",
    }),)


def _stage(effect, scope, plan, chosen, controller, order):
    slot = plan["slots"][len(chosen)]
    return FrozenMap({
        **effect, "plan": plan, "chosen": chosen, "controller": controller, "order": order,
        "player": scope.chooser(controller, slot["affected"]),
    })


@dataclass(frozen=True, slots=True)
class KeepPermanentsByTypeHandler:
    operation: str = KEEP_PERMANENTS_BY_TYPE_OPERATION
    handler_id: str = "choice.effect.keep_permanents_by_type.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("101.4", "101.4b", "608.2c", "701.21a")
    capability_dependencies: tuple[str, ...] = (KEEP_PERMANENTS_BY_TYPE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = tuple(sorted(_STAGE_FIELDS - {"op"}))
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("affected", "category", "prior_public_choices")
    mutation_path: tuple[str, ...] = ("choice.apnap.object-commit.v1",)
    replay_fixture: str = "keep-by-permanent-type"
    test_modules: tuple[str, ...] = ("tests.test_keep_permanents_by_type",)

    def prepare(self, effect, context):
        scope, plan, chosen, order = _choice_state(effect, context.stack_controller, context.query)
        if effect["player"] != context.actor:
            raise SemanticChoiceError("The keep-by-type chooser changed")
        if len(chosen) == len(plan["slots"]):
            followup = _commit_effects(plan, chosen, context.stack_controller)
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("Finish the complete public choice.", prepend_effects=followup),
            )
        stage = _stage(effect, scope, plan, chosen, context.stack_controller, order)
        if stage["player"] != context.actor:
            if "plan" in effect:
                raise SemanticChoiceError("The pending type choice changed its chooser")
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("Start with the first available type choice.", prepend_effects=(stage,)),
            )
        slot = plan["slots"][len(chosen)]
        previous = [
            {"affected": prior["affected"], "category": prior["category"], "permanent": ref}
            for prior, ref in zip(plan["slots"], chosen)
        ]
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=f"Choose one {slot['category']} controlled by {slot['affected']} to keep.",
                choice=ObjectChoice(
                    field_name="cards", legal_refs=tuple(slot["options"]), zones=("battlefield",),
                    minimum=1, maximum=1, visibility="public",
                    controller_relation="actor" if slot["affected"] == context.actor else "any",
                ),
                public_context=FrozenMap({
                    "affected": slot["affected"], "category": slot["category"], "prior_public_choices": previous,
                }),
            ),
            continuation_effect=stage,
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if set(effect) != _STAGE_FIELDS:
            raise SemanticChoiceError("The pending type choice is incomplete")
        controller = continuation.semantic_frame.controller
        scope, plan, chosen, order = _choice_state(effect, controller, query)
        if len(chosen) >= len(plan["slots"]):
            raise SemanticChoiceError("All printed type choices are already complete")
        slot = plan["slots"][len(chosen)]
        if effect["player"] != scope.chooser(controller, slot["affected"]):
            raise SemanticChoiceError("This player cannot choose for the affected permanent")
        submitted = response.get("cards")
        if (not isinstance(submitted, (list, tuple)) or len(submitted) != 1
                or type(submitted[0]) is not str or submitted[0] not in slot["options"]):
            raise SemanticChoiceError("Choose the exact one legal permanent for this type")
        chosen = (*chosen, submitted[0])
        followup = (_stage(effect, scope, plan, chosen, controller, order),) if len(chosen) < len(plan["slots"]) else _commit_effects(
            plan, chosen, controller,
        )
        return SemanticChoiceCompletion(prepend_effects=followup)
