from __future__ import annotations

"""Choose a currently owned physical commander and move through the zone owner."""

from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.commander_return_choice import COMMANDER_RETURN_FIELD, is_commander_return_effect
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from .model import AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest


def _eligible_commanders(query, actor):
    rows = sorted(
        (row for row in query.objects(zones=("command",), owner=actor)
         if row.commander and not row.token and row.known_to_actor),
        key=lambda row: row.ref,
    )
    if (any(row.owner != actor or row.zone != "command" or not row.logical_object_id or not row.ref for row in rows)
            or len({row.ref for row in rows}) != len(rows)):
        raise SemanticChoiceError("Commander return candidates changed their original public identities")
    return tuple(rows)


def _movement(actor, reference, label):
    return MoveObjectsSimultaneouslyIntent(
        actor=actor, object_refs=(reference,), expected_zones=("command",),
        destination="hand", reason=label, owned_only=True,
    )


def prepare_commander_return(effect, context):
    actor = context.actor
    if not is_commander_return_effect(effect, actor=actor) or actor != context.stack_controller:
        raise SemanticChoiceError("Commander return belongs to the controller of the resolving ability")
    rows = _eligible_commanders(context.query, actor)
    if len(rows) < 2:
        intents = (_movement(actor, rows[0].ref, context.stack_label),) if rows else ()
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect), preparation_intents=intents,
            auto_continue=AutoContinue(reason="Resolve the command-zone return with at most one commander"),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose one of your commanders to put into your hand.",
            choice=ObjectChoice(
                field_name="objects", legal_refs=tuple(row.ref for row in rows), zones=("command",),
                minimum=1, maximum=1, visibility="public", owner_relation="actor",
            ),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": effect["op"],
                "objects": [{"id": row.ref, "name": row.printed_name} for row in rows],
            }),
        ),
        continuation_effect=FrozenMap({
            **dict(effect), "_return_candidates": tuple((row.ref, row.logical_object_id) for row in rows),
            "_stack_label": context.stack_label,
        }),
    )


def complete_commander_return(continuation, response, query):
    effect = continuation.effect
    actor = continuation.semantic_frame.controller
    fields = {"op", "player", COMMANDER_RETURN_FIELD}
    if (set(effect) != fields | {"_return_candidates", "_stack_label"}
            or not is_commander_return_effect({key: effect[key] for key in fields}, actor=actor)
            or actor not in query.active_seats
            or type(effect["_stack_label"]) is not str or not effect["_stack_label"]):
        raise SemanticChoiceError("Commander return continuation lost its resolving controller")
    rows = _eligible_commanders(query, actor)
    current = tuple((row.ref, row.logical_object_id) for row in rows)
    if len(rows) < 2 or thaw_value(effect["_return_candidates"]) != thaw_value(current):
        raise SemanticChoiceError("The offered commanders changed before selection")
    objects = response.get("objects")
    if (not isinstance(objects, (list, tuple)) or len(objects) != 1
            or type(objects[0]) is not str or objects[0] not in {row.ref for row in rows}):
        raise SemanticChoiceError("Choose exactly one of your offered commanders")
    return SemanticChoiceCompletion(intents=(_movement(actor, objects[0], effect["_stack_label"]),))
