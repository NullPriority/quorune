from __future__ import annotations

"""Use the chosen opponent's public current power and canonical exile owner."""

from collections.abc import Mapping

from ..public_characteristic_aggregates import greatest_power_creatures
from ..replacement.immutable import FrozenMap
from ..rules.opponent_greatest_power_exile import GREATEST_POWER_EXILE_FIELD, validate_greatest_power_exile
from ..semantic_runtime.intents import ExilePermanentIntent
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


GREATEST_POWER_EXILE_CHOICES = "_greatest_power_exile_choices"
_FIELDS = frozenset({"op", "player", GREATEST_POWER_EXILE_FIELD})
_CONTINUATION_FIELDS = _FIELDS | {GREATEST_POWER_EXILE_CHOICES, "_stack_label"}


def _public_maxima(effect, actor, query):
    descriptor = effect.get(GREATEST_POWER_EXILE_FIELD)
    opponent = descriptor.get("opponent") if isinstance(descriptor, Mapping) else None
    if actor not in query.active_seats or opponent not in query.active_seats or actor == opponent:
        raise SemanticChoiceError("Greatest-power exile requires an active controller and announced opponent")
    try:
        validate_greatest_power_exile({key: effect[key] for key in _FIELDS}, actor=actor, opponent=opponent)
        rows = greatest_power_creatures(query.objects(zones=("battlefield",), controller=opponent))
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if any(not row.ref or not row.logical_object_id for row in rows):
        raise SemanticChoiceError("The greatest-power creature identity is unavailable")
    return tuple(sorted(rows, key=lambda row: row.ref))


def _identities(rows):
    return tuple((row.ref, row.logical_object_id) for row in rows)


def prepare_greatest_power_exile(effect, context):
    actor = context.actor
    if actor != context.stack_controller or set(effect) != _FIELDS:
        raise SemanticChoiceError("Greatest-power exile requires the resolving controller and its complete instruction")
    rows = _public_maxima(effect, actor, context.query)
    if len(rows) < 2:
        intents = (ExilePermanentIntent(actor=actor, object_ref=rows[0].ref, reason=context.stack_label),) if rows else ()
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect), preparation_intents=intents,
            auto_continue=AutoContinue(reason="Resolve the current greatest-power creature exile."),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose a tied greatest-power creature controlled by the targeted opponent.",
            choice=ObjectChoice(field_name="objects", legal_refs=tuple(row.ref for row in rows),
                                zones=("battlefield",), controller_relation="opponent"),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": effect["op"],
                "objects": [{"id": row.ref, "name": row.printed_name} for row in rows],
            }),
        ),
        continuation_effect=FrozenMap({
            **dict(effect), GREATEST_POWER_EXILE_CHOICES: _identities(rows), "_stack_label": context.stack_label,
        }),
    )


def complete_greatest_power_exile(continuation, response, query):
    effect = continuation.effect
    if set(effect) != _CONTINUATION_FIELDS:
        raise SemanticChoiceError("Greatest-power exile continuation fields changed")
    actor, label = effect["player"], effect["_stack_label"]
    if type(label) is not str or not label:
        raise SemanticChoiceError("Greatest-power exile requires its resolving stack label")
    rows = _public_maxima(effect, actor, query)
    if len(rows) < 2 or _identities(rows) != effect[GREATEST_POWER_EXILE_CHOICES]:
        raise SemanticChoiceError("The greatest-power exile choices changed")
    selected = response.get("objects", ())
    if (not isinstance(selected, (list, tuple)) or len(selected) != 1
            or type(selected[0]) is not str or selected[0] not in {row.ref for row in rows}):
        raise SemanticChoiceError("Choose one of the currently tied creatures")
    return SemanticChoiceCompletion(
        intents=(ExilePermanentIntent(actor=actor, object_ref=selected[0], reason=label),),
    )
