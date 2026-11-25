from __future__ import annotations

"""Choose public opponent maxima, then commit one canonical destruction batch."""

from ..public_characteristic_aggregates import greatest_power_creatures
from ..replacement.immutable import FrozenMap
from ..rules.opponent_greatest_power_destruction import (
    GREATEST_POWER_DESTRUCTION_FIELD, validate_greatest_power_destruction,
)
from ..semantic_runtime.intents import DestroyPermanentTargetsIntent
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


GREATEST_POWER_DESTRUCTION_SELECTIONS = "_greatest_power_destruction_selections"
GREATEST_POWER_DESTRUCTION_OFFER = "_greatest_power_destruction_offer"
_FIELDS = frozenset({"op", "player", GREATEST_POWER_DESTRUCTION_FIELD})
_PROGRESS = _FIELDS | {GREATEST_POWER_DESTRUCTION_SELECTIONS}


def _choice_state(effect, query, actor):
    try:
        validate_greatest_power_destruction({key: effect[key] for key in _FIELDS}, actor=actor)
        options = {
            row.ref: row
            for seat in query.active_seats if seat != actor
            for row in greatest_power_creatures(query.objects(zones=("battlefield",), controller=seat))
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if any(not row.ref or not row.logical_object_id for row in options.values()):
        raise SemanticChoiceError("A greatest-power creature lacks a current public identity")
    raw = effect.get(GREATEST_POWER_DESTRUCTION_SELECTIONS, ())
    if not isinstance(raw, (list, tuple)):
        raise SemanticChoiceError("Greatest-power destruction selections are malformed")
    selections = []
    chosen_seats = set()
    for choice in raw:
        if (not isinstance(choice, (list, tuple)) or len(choice) != 3
                or any(type(value) is not str or not value for value in choice)):
            raise SemanticChoiceError("A greatest-power selection requires its controller and incarnation")
        seat, ref, logical = choice
        row = options.get(ref)
        if seat in chosen_seats or row is None or (row.controller, row.logical_object_id) != (seat, logical):
            raise SemanticChoiceError("A greatest-power selection no longer matches its opponent and incarnation")
        selections.append((seat, ref, logical))
        chosen_seats.add(seat)
    remaining = tuple(options[ref] for ref in sorted(options) if options[ref].controller not in chosen_seats)
    return tuple(selections), remaining


def _offer(rows):
    return tuple((row.controller, row.ref, row.logical_object_id) for row in rows)


def prepare_greatest_power_destruction(effect, context):
    actor = context.actor
    if actor != context.stack_controller or actor not in context.query.active_seats or set(effect) not in (_FIELDS, _PROGRESS):
        raise SemanticChoiceError("Greatest-power destruction requires the active resolving controller")
    selections, rows = _choice_state(effect, context.query, actor)
    if not rows:
        refs = tuple(ref for _, ref, _ in selections)
        intents = (DestroyPermanentTargetsIntent(actor=actor, object_refs=refs, reason=context.stack_label),) if refs else ()
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect), preparation_intents=intents,
            auto_continue=AutoContinue(reason="Finish the simultaneous destruction of the chosen creatures."),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose a greatest-power creature for one remaining opponent.",
            choice=ObjectChoice(field_name="objects", legal_refs=tuple(row.ref for row in rows),
                                zones=("battlefield",), controller_relation="opponent"),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": effect["op"],
                "objects": [{"id": row.ref, "name": row.printed_name} for row in rows],
            }),
        ),
        continuation_effect=FrozenMap({
            **dict(effect),
            GREATEST_POWER_DESTRUCTION_SELECTIONS: selections,
            GREATEST_POWER_DESTRUCTION_OFFER: _offer(rows),
        }),
    )


def complete_greatest_power_destruction(continuation, response, query):
    effect = continuation.effect
    if set(effect) != _PROGRESS | {GREATEST_POWER_DESTRUCTION_OFFER} or effect["player"] not in query.active_seats:
        raise SemanticChoiceError("Greatest-power destruction continuation is malformed")
    selections, rows = _choice_state(effect, query, effect["player"])
    if effect[GREATEST_POWER_DESTRUCTION_OFFER] != _offer(rows):
        raise SemanticChoiceError("Greatest-power destruction options changed")
    picked = response.get("objects", ())
    if not isinstance(picked, (list, tuple)) or len(picked) != 1 or type(picked[0]) is not str:
        raise SemanticChoiceError("Choose exactly one offered creature")
    selected = next((row for row in rows if row.ref == picked[0]), None)
    if selected is None:
        raise SemanticChoiceError("Choose a greatest-power creature for a remaining opponent")
    next_effect = {key: effect[key] for key in _FIELDS}
    next_effect[GREATEST_POWER_DESTRUCTION_SELECTIONS] = (
        *selections, (selected.controller, selected.ref, selected.logical_object_id),
    )
    return SemanticChoiceCompletion(prepend_effects=(FrozenMap(next_effect),))
