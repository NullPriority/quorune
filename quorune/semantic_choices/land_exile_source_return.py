"""Return the original source only after the captured card is publicly exiled as land."""

from ..discover_model import discover_options_fingerprint, discover_zone_counter
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..semantic_runtime.zone_move_intent import ZoneMoveIntent
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _source_identity(instruction, query):
    ref = instruction["source"]
    if ref is None:
        return None
    source = query.object(ref, zones=("battlefield",))
    if source is None or source.phased_out:
        return None
    return LibrarySelectionObjectIdentity(source.object_id, source.logical_object_id, source.ref)


def _public_land(rows, query):
    if not rows:
        return False
    (owner, identity), = rows
    card = query.object(identity.ref, zones=("exile",))
    return (card is not None and card.owner == owner and card.object_id == identity.object_id
            and card.logical_object_id == identity.logical_object_id and card.known_to_actor
            and not card.token and not card.face_down and "land" in card.types)


def _source_fingerprint(identity):
    return discover_options_fingerprint((identity.to_dict(),))


def prepare_land_exile_source_return(effect, context, rows):
    source = _source_identity(effect["instruction"], context.query)
    if (source is None or source.logical_object_id != context.source_logical_object_id
            or not _public_land(rows, context.query)):
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The public exile result does not permit the original source to return."),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Return the ability source to its owner's hand?",
            choice=ScalarChoice("choice", ("return", "decline")),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": effect["op"], "source": source.ref}),
        ),
        continuation_effect=FrozenMap({
            **effect, "_stack_ref": context.stack_ref, "_options_fingerprint": _source_fingerprint(source),
        }),
    )


def complete_land_exile_source_return(effect, response, query, fingerprint):
    choice = response.get("choice")
    if choice == "decline":
        return SemanticChoiceCompletion()
    source = _source_identity(effect["instruction"], query)
    if choice != "return" or source is None or _source_fingerprint(source) != fingerprint:
        raise SemanticChoiceError("The original ability source is no longer available for this return")
    return SemanticChoiceCompletion(intents=(ZoneMoveIntent(
        actor=effect["instruction"]["player"], object_ref=source.ref, expected_zones=("battlefield",),
        destination="hand", reason="Return the original ability source.",
        expected_zone_change_counter=discover_zone_counter(source),
    ),))
