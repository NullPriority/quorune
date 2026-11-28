"""Prepare the next library-top exile through the canonical movement owner."""

from dataclasses import dataclass

from ..carddb_characteristics import library_card_rules_names
from ..replacement.immutable import FrozenMap
from ..rules.exile_until_same_name import (
    EXILE_UNTIL_SAME_NAME_CAPABILITY, EXILE_UNTIL_SAME_NAME_OPERATION,
    ExiledNameHistory, validate_exile_until_same_name,
)
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent, ZoneMoveIntent
from .fixed_top_exile import _capture_library_tops
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


EXILE_UNTIL_SAME_NAME_OFFER_OPERATION = "offer_repeated_library_exile_card"


def prepare_exile_until_same_name(effect, context):
    if context.actor != context.stack_controller:
        raise SemanticChoiceError("Repeating library-top exile belongs to the resolving controller")
    instruction = {key: value for key, value in effect.items() if key != "_name_history"}
    try:
        validate_exile_until_same_name(instruction, player=context.actor)
        history = ExiledNameHistory.from_dict(effect["_name_history"]) if "_name_history" in effect else ExiledNameHistory()
    except (TypeError, ValueError) as error:
        raise SemanticChoiceError(str(error)) from error

    candidate = next(_capture_library_tops(context.query, context.actor, 1), None)
    if candidate is None:
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="The resolving controller's library is empty."),
        )

    exile = MoveObjectsSimultaneouslyIntent(
        context.actor, (candidate.ref,), ("library",), "exile", "Exile the top card of the library",
    )
    offer = FrozenMap({
        "op": EXILE_UNTIL_SAME_NAME_OFFER_OPERATION,
        "instruction": instruction,
        "candidate": candidate.to_dict(),
        "name_history": history.to_dict(),
    })
    return SemanticChoicePreparation(
        request=None,
        continuation_effect=FrozenMap(effect),
        preparation_intents=(exile,),
        auto_continue=AutoContinue(reason="Exile the captured top card.", prepend_effects=(offer,)),
    )



def _decode_offer(effect, actor):
    if set(effect) != {"op", "instruction", "candidate", "name_history"}:
        raise SemanticChoiceError("Repeated exile offer fields have changed")
    if effect["op"] != EXILE_UNTIL_SAME_NAME_OFFER_OPERATION:
        raise SemanticChoiceError("Repeated exile offer has an unknown operation")
    try:
        validate_exile_until_same_name(effect["instruction"], player=actor)
        return (
            LibrarySelectionObjectIdentity.from_dict(effect["candidate"]),
            ExiledNameHistory.from_dict(effect["name_history"]),
        )
    except (TypeError, ValueError) as error:
        raise SemanticChoiceError(str(error)) from error


def _exile_arrival(query, actor, identity):
    card = query.object(identity.ref, zones=("exile",))
    if (
        card is None or card.owner != actor or card.token
        or card.object_id != identity.object_id or card.logical_object_id != identity.logical_object_id
    ):
        return None
    return card


def _next_exile(effect, history):
    return FrozenMap({**effect["instruction"], "_name_history": history.to_dict()})


def prepare_exile_offer(effect, context):
    if context.actor != context.stack_controller:
        raise SemanticChoiceError("The exiled card belongs to the resolving controller's choice")
    identity, history = _decode_offer(effect, context.actor)
    card = _exile_arrival(context.query, context.actor, identity)
    if card is None:
        if context.query.library_refs(context.actor, top_first=True)[:1] == (identity.ref,):
            raise SemanticChoiceError("Repeating the instruction requires a committed library departure")
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="The top card did not arrive in exile.",
                                       prepend_effects=(_next_exile(effect, history),)),
        )
    updated = history.after_exile(context.query.public_exile_rules_names(card.ref))
    if updated is None:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="The exiled card shares a name with an earlier exiled card."),
        )
    request = SemanticChoiceRequest(
        prompt="Put the exiled card into your hand or continue exiling.",
        choice=ObjectChoice(field_name="objects", legal_refs=(card.ref,), zones=("exile",),
                            minimum=0, maximum=1, optional=True, owner_relation="you"),
        public_context=FrozenMap({"stack": context.stack_ref,
                                 "objects": [{"id": card.ref, "name": card.printed_name
                                              if card.known_to_actor and not card.face_down else "Exiled card"}]}),
    )
    return SemanticChoicePreparation(request=request, continuation_effect=FrozenMap(effect))


def complete_exile_offer(continuation, response, query):
    effect = continuation.effect
    actor = effect["instruction"].get("player")
    if actor not in query.active_seats:
        raise SemanticChoiceError("The resolving controller no longer has an exile choice")
    identity, history = _decode_offer(effect, actor)
    if _exile_arrival(query, actor, identity) is None:
        raise SemanticChoiceError("The offered exile card no longer has its captured identity")
    updated = history.after_exile(query.public_exile_rules_names(identity.ref))
    if updated is None:
        raise SemanticChoiceError("An already seen rules name ends this instruction")
    selection = response.get("objects", ())
    if not isinstance(selection, (tuple, list)) or tuple(selection) not in ((), (identity.ref,)):
        raise SemanticChoiceError("Choose the captured exile card or continue exiling")
    if not selection:
        return SemanticChoiceCompletion(prepend_effects=(_next_exile(effect, updated),))
    return SemanticChoiceCompletion(intents=(
        ZoneMoveIntent(actor, identity.ref, ("exile",), "hand", "Put the exiled card into your hand",
                       owned_only=True),
    ))


def materialize_exile_offer_names(host, effect, actor):
    if effect.get("op") != EXILE_UNTIL_SAME_NAME_OFFER_OPERATION:
        return FrozenMap({})
    identity, _history = _decode_offer(effect, actor)
    card = host.state.cards.get(identity.object_id)
    if (
        card is None or card.zone != "exile" or card.owner != actor or card.ref != identity.ref
        or card.logical_object_id != identity.logical_object_id or card.is_token
    ):
        return FrozenMap({})
    if card.face_down:
        return FrozenMap({card.ref: ()})
    record = host.card_record(card)
    names = library_card_rules_names(record) if record is not None else (card.name,)
    return FrozenMap({card.ref: names})


@dataclass(frozen=True, slots=True)
class RepeatedLibraryExileHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 201.2a", "CR 400.2", "CR 406.2", "CR 608.2c")
    capability_dependencies: tuple[str, ...] = (EXILE_UNTIL_SAME_NAME_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("instruction", "candidate", "name_history", "_name_history")
    private_data: tuple[str, ...] = ("unexiled library cards",)
    projected_fields: tuple[str, ...] = ("prompt", "objects", "legal_actions.choice_schema.legal_refs")
    mutation_path: tuple[str, ...] = ("MoveObjectsSimultaneouslyIntent", "ZoneMoveIntent")
    replay_fixture: str = "repeated-library-top-exile"
    test_modules: tuple[str, ...] = ("tests.test_exile_until_same_name",)

    def prepare(self, effect, context):
        try:
            if self.operation == EXILE_UNTIL_SAME_NAME_OPERATION:
                return prepare_exile_until_same_name(effect, context)
            return prepare_exile_offer(effect, context)
        except (TypeError, ValueError, KeyError) as error:
            raise SemanticChoiceError(str(error)) from error

    def complete(self, continuation, response, query):
        if self.operation != EXILE_UNTIL_SAME_NAME_OFFER_OPERATION:
            raise SemanticChoiceError("A mandatory top exile accepts no choice")
        try:
            return complete_exile_offer(continuation, response, query)
        except (TypeError, ValueError, KeyError) as error:
            raise SemanticChoiceError(str(error)) from error


REPEATED_LIBRARY_EXILE_HANDLERS = (
    RepeatedLibraryExileHandler(EXILE_UNTIL_SAME_NAME_OPERATION, "choice.library.repeated-top-exile.v1"),
    RepeatedLibraryExileHandler(EXILE_UNTIL_SAME_NAME_OFFER_OPERATION, "choice.library.take-or-repeat-exile.v1"),
)
