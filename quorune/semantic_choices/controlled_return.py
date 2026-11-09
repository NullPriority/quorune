from __future__ import annotations

"""Offer and commit a bounded return using the immutable public choice query."""

from ..object_query import object_matches_query
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.controlled_return import CONTROLLED_RETURN_FIELD, ControlledReturn
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from .model import (
    AutoContinue,
    ObjectChoice,
    SemanticChoiceCompletion,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _scope(effect, actor):
    try:
        return ControlledReturn.from_effect(effect, actor=actor)
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _candidates(query, predicate):
    return tuple(sorted(
        (row.ref, row.logical_object_id)
        for row in query.objects(zones=("battlefield",), controller=predicate.controller)
        if object_matches_query(row, predicate)
    ))


def prepare_controlled_return(effect, context):
    scope = _scope(effect, context.actor)
    candidates = _candidates(context.query, scope.predicate)
    if not candidates:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="no eligible permanent to return"),
        )
    maximum = min(scope.maximum, len(candidates))
    minimum = min(scope.minimum, maximum)
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose the permanents to return to their owners' hands.",
            choice=ObjectChoice(
                field_name="objects", legal_refs=tuple(ref for ref, _ in candidates),
                zones=("battlefield",), minimum=minimum, maximum=maximum,
                optional=minimum == 0, visibility="public", controller_relation="actor",
            ),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"]}),
        ),
        continuation_effect=FrozenMap({
            **dict(effect), "_choice_actor": context.actor, "_stack_label": context.stack_label,
            "_return_candidates": candidates,
        }),
    )


def controlled_return_intent(actor, selected, label):
    return MoveObjectsSimultaneouslyIntent(
        actor=actor, object_refs=selected, expected_zones=("battlefield",), destination="hand",
        reason=label, controlled_only=True,
    )


def complete_controlled_return(continuation, response, query):
    effect = continuation.effect
    if set(effect) != {"op", CONTROLLED_RETURN_FIELD, "_choice_actor", "_stack_label", "_return_candidates"}:
        raise SemanticChoiceError("Controlled return continuation is malformed")
    actor = effect["_choice_actor"]
    scope = _scope({"op": effect["op"], CONTROLLED_RETURN_FIELD: effect[CONTROLLED_RETURN_FIELD]}, actor)
    current = _candidates(query, scope.predicate)
    saved = thaw_value(effect["_return_candidates"])
    if saved != thaw_value(current) or actor not in query.active_seats:
        raise SemanticChoiceError("Controlled return candidates or controller changed")
    selected = response.get("objects", ())
    legal = {ref for ref, _ in current}
    maximum = min(scope.maximum, len(legal))
    minimum = min(scope.minimum, maximum)
    if (
        not isinstance(selected, (list, tuple))
        or not minimum <= len(selected) <= maximum
        or any(type(ref) is not str or ref not in legal for ref in selected)
        or len(selected) != len(set(selected))
    ):
        raise SemanticChoiceError("Controlled return selection is outside its sealed bounds")
    if not selected:
        return SemanticChoiceCompletion()
    return SemanticChoiceCompletion(intents=(controlled_return_intent(actor, tuple(selected), effect["_stack_label"]),))
