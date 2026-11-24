"""Issue the optional play after the shared-type reveal is committed."""

from collections.abc import Mapping

from ..discover_model import discover_options_fingerprint
from ..replacement.immutable import FrozenMap
from ..rules.shared_type_library_play_model import (
    ResolvingSharedTypeLibraryPlayIntent, revealed_shared_type_card, unissued_shared_type_play,
)
from .model import (
    AutoContinue, CastOrDeclineChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def prepare_shared_type_play(effect, context):
    try:
        revealed_shared_type_card(effect, actor=context.actor)
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if context.actor != context.stack_controller:
        raise SemanticChoiceError("The revealed card play belongs to the resolving controller")
    options = tuple(context.query.authorized_cast_options())
    if not options:
        return SemanticChoicePreparation(None, FrozenMap(effect),
            auto_continue=AutoContinue(reason="The revealed card cannot be played"))
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Play the revealed card without paying its mana cost?",
            choice=CastOrDeclineChoice(options, selection="play"),
            public_context=FrozenMap({"stack": context.stack_ref, "cast_options": list(options)}),
        ),
        continuation_effect=FrozenMap({**effect, "_stack_ref": context.stack_ref,
            "_options_fingerprint": discover_options_fingerprint(options)}),
    )


def complete_shared_type_play(continuation, response, query):
    effect = continuation.effect
    actor = continuation.semantic_frame.controller
    try:
        revealed_shared_type_card(effect, actor=actor, issued_stack=continuation.stack_ref)
        if actor not in query.active_seats:
            raise ValueError("The controller can no longer play the revealed card")
        action = response.get("choice")
        fields = {key: value for key, value in response.items()
                  if key not in {"action", "action_id", "choice", "choice_schema", "cast"}}
        submitted = response.get("cast")
        if submitted is not None and (not isinstance(submitted, Mapping) or fields.keys() & submitted.keys()):
            raise ValueError("The play response must supply each field once")
        selected = {**(submitted or {}), **fields}
        if action == "decline" and not selected:
            return SemanticChoiceCompletion()
        options = tuple(query.authorized_cast_options())
        if action != "play" or discover_options_fingerprint(options) != effect["_options_fingerprint"]:
            raise ValueError("Choose one current revealed-card play")
        requested = selected.get("cost_option")
        legal = [option for option in options if requested is None or option["id"] == requested]
        if len(legal) != 1:
            raise ValueError("Choose one issued face and price")
        return SemanticChoiceCompletion(intents=(ResolvingSharedTypeLibraryPlayIntent(
            actor, continuation.stack_ref, unissued_shared_type_play(effect),
            effect["_options_fingerprint"], FrozenMap({**selected, "cost_option": legal[0]["id"]}),
        ),))
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
