"""Public APNAP creature-type choices followed by a single graveyard return batch."""

from dataclasses import replace

from ..creature_subtypes import canonical_creature_subtype
from ..public_zone_moves import PublicBattlefieldController
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import RecordChoiceIntent
from ..semantic_runtime.zone_move_intent import MultiownerFrontFaceGraveyardReturnIntent
from .model import SemanticChoiceCompletion, SemanticChoiceError, SemanticChoicePreparation


def _stage(effect, query, stack_ref):
    order = tuple(query.apnap_order)
    choices = effect.get("_type_return_choices", ())
    if (not order or not isinstance(choices, (tuple, list)) or len(choices) >= len(order)
            or any(type(value) is not str or canonical_creature_subtype(value) != value for value in choices)):
        raise SemanticChoiceError("The public type choices changed their APNAP order")
    if choices and (tuple(effect.get("_type_return_order", ())) != order
                    or effect.get("_type_return_stack") != stack_ref
                    or effect.get("player") != order[len(choices)]):
        raise SemanticChoiceError("The typed return lost its original choices")
    return order, tuple(choices)


def prepare_type_return(effect, context, request):
    order, choices = _stage(effect, context.query, context.stack_ref)
    actor = order[len(choices)]
    if context.actor != actor:
        raise SemanticChoiceError("Only the current APNAP chooser may select a type")
    base = request(context.stack_ref)
    return SemanticChoicePreparation(
        request=replace(base, public_context=FrozenMap({
            **base.public_context, "previous_choices": [
                {"player": seat, "creature_type": kind} for seat, kind in zip(order, choices)
            ],
        })),
        continuation_effect=FrozenMap({
            **effect, "player": actor, "_type_return_order": order,
            "_type_return_stack": context.stack_ref, "_type_return_choices": choices,
        }),
    )


def complete_type_return(continuation, response, query):
    effect = continuation.effect
    order, choices = _stage(effect, query, continuation.stack_ref)
    raw = response.get("creature_type")
    choice = canonical_creature_subtype(raw) if type(raw) is str else None
    if (set(response) - {"creature_type", "action", "action_id", "choice_schema"}
            or response.get("action", "choose") != "choose" or choice is None):
        raise SemanticChoiceError("Choose one creature type from the public catalog")
    actor = order[len(choices)]
    record = RecordChoiceIntent(actor, "choice.creature_type", f"{actor} chose {choice}.",
                                FrozenMap({"player": actor, "creature_type": choice}))
    choices = (*choices, choice)
    if len(choices) < len(order):
        return SemanticChoiceCompletion(intents=(record,), repeat_effect=FrozenMap({
            **effect, "_type_return_choices": choices, "player": order[len(choices)],
        }))
    union = set(choices)
    refs = tuple(row.ref for seat in order for row in query.objects(zones=("graveyard",), owner=seat)
                 if row.physical_card and not row.face_down and "creature" in row.types
                 and (union.intersection(row.subtypes) or "changeling" in {k.casefold() for k in row.keywords}))
    if not refs:
        return SemanticChoiceCompletion(intents=(record,))
    return SemanticChoiceCompletion(intents=(record, MultiownerFrontFaceGraveyardReturnIntent(
        actor=continuation.semantic_frame.controller, object_refs=refs,
        expected_zones=("graveyard",), destination="battlefield", reason="Selected creature type union",
        battlefield_controller=PublicBattlefieldController.OWNER,
    ),))
