"""Issue the optional play after the shared-type reveal is committed."""

from collections.abc import Mapping

from ..discover_model import discover_options_fingerprint
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionArrangement
from ..semantic_runtime.library_selection_intent import LibrarySelectionIntent
from .fixed_library_selection import _current_library_top, _identity
from ..rules.shared_type_library_play_model import (
    ResolvingSharedTypeLibraryPlayIntent, revealed_shared_type_card, unissued_shared_type_play,
)
from .model import (
    AutoContinue, CastOrDeclineChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


def _bottom_revealed_card(query, *, actor, stack_ref, reason, identity):
    top = _current_library_top(query, actor)
    if top is None or _identity(top) != identity:
        raise ValueError("The public revealed-card incarnation changed before its bottom result")
    return LibrarySelectionIntent(
        actor=actor, player=actor, arrangement=LibrarySelectionArrangement(
            looked=(identity,), selected_refs=(), remainder_refs=(identity.ref,),
            remainder_destination="library_bottom", remainder_order="chosen",
        ), reason=reason, source_stack_ref=stack_ref, looked_are_public=True, selected_are_public=False,
    )


def prepare_shared_type_play(effect, context):
    try:
        identity, comparison = revealed_shared_type_card(effect, actor=context.actor)
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if context.actor != context.stack_controller:
        raise SemanticChoiceError("The revealed card play belongs to the resolving controller")
    options = tuple(context.query.authorized_cast_options())
    mandatory = comparison is None
    selection = "cast" if mandatory else "play"
    if not options:
        try:
            intents = (_bottom_revealed_card(
                context.query, actor=context.actor, stack_ref=context.stack_ref, reason=context.stack_label,
                identity=identity,
            ),) if mandatory else ()
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(None, FrozenMap(effect), preparation_intents=intents,
            auto_continue=AutoContinue(reason="The revealed card cannot be " + ("cast" if mandatory else "played")))
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f"{selection.title()} the revealed card without paying its mana cost?",
            choice=CastOrDeclineChoice(options, selection=selection),
            public_context=FrozenMap({"stack": context.stack_ref, "cast_options": list(options)}),
        ),
        continuation_effect=FrozenMap({**effect, "_stack_ref": context.stack_ref,
            "_options_fingerprint": discover_options_fingerprint(options)}),
    )


def complete_shared_type_play(continuation, response, query):
    effect = continuation.effect
    actor = continuation.semantic_frame.controller
    try:
        identity, comparison = revealed_shared_type_card(effect, actor=actor, issued_stack=continuation.stack_ref)
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
            intents = (_bottom_revealed_card(
                query, actor=actor, stack_ref=continuation.stack_ref, reason=effect["_stack_label"], identity=identity,
            ),) if comparison is None else ()
            return SemanticChoiceCompletion(intents=intents)
        selection = "cast" if comparison is None else "play"
        options = tuple(query.authorized_cast_options())
        if action != selection or discover_options_fingerprint(options) != effect["_options_fingerprint"]:
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
