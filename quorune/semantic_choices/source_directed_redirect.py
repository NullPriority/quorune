"""Choose one of the server-validated slots for a source-directed change."""

from __future__ import annotations

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import RetargetStackItemIntent
from ..rules.source_directed_redirect_model import source_directed_redirect_from_effect
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _spec(effect, *, continuation=False):
    try:
        return source_directed_redirect_from_effect(effect, resolved=True, continuation=continuation)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def prepare_source_directed_redirect(effect, context):
    spec = _spec(effect)
    target = context.query.stack_object(effect["stack"], exclude_ref=context.stack_ref)
    bundle = context.query.stack_target_schema(target.ref, actor=target.controller) if target is not None else None
    if bundle is None:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("No target can be changed to the original source."),
        )
    schema = bundle.get("authoritative")
    slots = tuple(bundle.get("redirect_slots") or ())
    if not isinstance(schema, Mapping) or not slots:
        raise SemanticChoiceError("The redirect has no authoritative target slots")
    choices = (*slots, "decline") if spec.optional else slots
    stored = FrozenMap({
        **dict(effect), "_target_stack_ref": target.ref, "_target_schema": dict(schema),
        "_choice_actor": context.actor, "_validation_actor": target.controller, "_redirect_slots": slots,
    })
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose one target to change to the original source.",
            choice=ScalarChoice(field_name="choice", legal_values=choices),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": effect["op"], "target_stack": target.ref,
                "recipient": effect["recipient"], "redirect_slots": slots,
            }),
        ),
        continuation_effect=stored,
    )


def complete_source_directed_redirect(continuation, response, query):
    effect = continuation.effect
    spec = _spec(effect, continuation=True)
    actor = effect["_choice_actor"]
    if actor != continuation.semantic_frame.controller or actor not in query.active_seats:
        raise SemanticChoiceError("The original redirect controller is unavailable")
    if "targets" in response:
        raise SemanticChoiceError("A source-directed change chooses only one offered slot")
    selected = response.get("choice")
    if selected == "decline" and spec.optional:
        return SemanticChoiceCompletion()
    if type(selected) is not str or selected not in effect["_redirect_slots"]:
        raise SemanticChoiceError("Choose one originally offered target slot")
    target = query.stack_object(effect["_target_stack_ref"])
    if target is None or target.ref != effect["stack"] or target.controller != effect["_validation_actor"]:
        raise SemanticChoiceError("The original stack target is unavailable")
    current = query.stack_target_schema(target.ref, actor=target.controller)
    if (current is None or current.get("authoritative") != effect["_target_schema"]
            or selected not in current.get("redirect_slots", ())):
        raise SemanticChoiceError("The selected source redirect is no longer legal")
    try:
        targets, groups = query.validate_stack_targets(
            target.ref, selected, actor=target.controller, target_schema=effect["_target_schema"],
        )
        snapshots = query.copied_target_snapshots(target.ref, actor=target.controller)
    except ValueError as exc:
        raise SemanticChoiceError("The selected source redirect is no longer legal") from exc
    return SemanticChoiceCompletion(intents=(RetargetStackItemIntent(
        actor=actor, target_stack_ref=target.ref, targets=targets, target_groups=FrozenMap(groups),
        target_snapshots=FrozenMap(snapshots), source_stack_ref=continuation.stack_ref,
    ),))
