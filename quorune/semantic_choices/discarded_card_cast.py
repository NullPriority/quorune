from __future__ import annotations

"""The private discard and the immediate optional cast share one original resolution."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..discover_model import discover_options_fingerprint
from ..replacement.immutable import FrozenMap
from ..rules.discarded_card_cast import (
    DISCARDED_CARD_CAST, DISCARDED_CARD_CAST_CAPABILITY, DiscardedCardCastIntent,
    _DISCARD_FIELD, _OPTIONS_FIELD, discarded_card_cast_instruction, validate_discarded_card_identity,
)
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..zone_trigger_events import ZoneTransitionKind
from .model import (
    AutoContinue, CastOrDeclineChoice, ObjectChoice, SemanticChoiceCompletion,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


_HAND_FIELD = "_hand_incarnations"
_LABEL_FIELD = "_stack_label"
_RESPONSE_FIELDS = {"action", "action_id", "choice_schema"}


def _validate(effect, actor, extra=()):
    base = discarded_card_cast_instruction(actor)
    if (set(effect) != set(base).union(extra) or type(effect.get("schema_version")) is not int
            or any(effect.get(key) != value for key, value in base.items())):
        raise SemanticChoiceError("Discard casting has an invalid resolving continuation")


def _auto(effect, reason):
    return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue(reason=reason))


def _prepare_discard(effect, context):
    _validate(effect, context.actor)
    cards = tuple(sorted(context.query.objects(zones=("hand",), owner=context.actor), key=lambda row: row.ref))
    if not cards:
        return _auto(effect, "No hand card can be discarded")
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Discard a card.",
            choice=ObjectChoice(field_name="cards", legal_refs=tuple(card.ref for card in cards), zones=("hand",),
                                visibility="actor_private", owner_relation="actor"),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": DISCARDED_CARD_CAST,
                "cards": [{"ref": card.ref, "name": card.printed_name} for card in cards],
            }),
        ),
        continuation_effect=FrozenMap({
            **effect, _HAND_FIELD: {card.ref: card.logical_object_id for card in cards}, _LABEL_FIELD: context.stack_label,
        }),
    )


def _complete_discard(continuation, response, query):
    effect = continuation.effect
    actor = effect["player"]
    _validate(effect, actor, (_HAND_FIELD, _LABEL_FIELD))
    submitted = response.get("cards")
    if (set(response) - (_RESPONSE_FIELDS | {"cards"}) or not isinstance(submitted, (list, tuple))
            or len(submitted) != 1 or type(submitted[0]) is not str):
        raise SemanticChoiceError("Discard exactly one of the offered cards")
    ref = submitted[0]
    hand = effect[_HAND_FIELD]
    if not isinstance(hand, Mapping) or type(effect[_LABEL_FIELD]) is not str or not effect[_LABEL_FIELD]:
        raise SemanticChoiceError("The retained discard choice is malformed")
    card = query.object(ref, zones=("hand",))
    if card is None or card.owner != actor or not card.logical_object_id or hand.get(ref) != card.logical_object_id:
        raise SemanticChoiceError("The offered hand incarnation changed")
    return SemanticChoiceCompletion(
        intents=(MoveObjectsSimultaneouslyIntent(
            actor=actor, object_refs=(ref,), expected_zones=("hand",), destination="graveyard",
            reason=effect[_LABEL_FIELD], transition_kind=ZoneTransitionKind.DISCARD, owned_only=True,
            discard_effect_origin=query.discard_effect_origin,
        ),),
        prepend_effects=(FrozenMap({
            **discarded_card_cast_instruction(actor),
            _DISCARD_FIELD: {"previous": card.logical_object_id, "ref": ref},
        }),),
    )


def _prepare_cast(effect, context):
    _validate(effect, context.actor, (_DISCARD_FIELD,))
    validate_discarded_card_identity(effect[_DISCARD_FIELD])
    options = tuple(context.query.authorized_cast_options())
    if not options:
        return _auto(effect, "The discarded card has no legal cast from this resolution")
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Cast the discarded card without paying its mana cost?",
            choice=CastOrDeclineChoice(options),
            public_context=FrozenMap({
                "stack": context.stack_ref, "operation": DISCARDED_CARD_CAST,
                "card": effect[_DISCARD_FIELD]["ref"], "cast_options": options,
            }),
        ),
        continuation_effect=FrozenMap({**effect, _OPTIONS_FIELD: discover_options_fingerprint(options)}),
    )


def _complete_cast(continuation, response, query):
    effect = continuation.effect
    _validate(effect, effect["player"], (_DISCARD_FIELD, _OPTIONS_FIELD))
    options = tuple(query.authorized_cast_options())
    if not options or discover_options_fingerprint(options) != effect[_OPTIONS_FIELD]:
        raise SemanticChoiceError("The discarded card's legal casting options changed")
    choice = response.get("choice")
    if choice == "decline":
        if set(response) - (_RESPONSE_FIELDS | {"choice"}):
            raise SemanticChoiceError("Decline does not submit any spell information")
        return SemanticChoiceCompletion()
    cast = response.get("cast", {})
    if choice != "cast" or not isinstance(cast, Mapping):
        raise SemanticChoiceError("Select an offered spell cast or decline")
    submitted = {**cast, **{key: value for key, value in response.items()
                           if key not in (_RESPONSE_FIELDS | {"choice", "cast"})}}
    return SemanticChoiceCompletion(intents=(DiscardedCardCastIntent(
        continuation.stack_ref, effect["player"], effect[_DISCARD_FIELD], effect[_OPTIONS_FIELD], FrozenMap(submitted),
    ),))


@dataclass(frozen=True, slots=True)
class DiscardedCardCastChoiceHandler:
    operation: str = DISCARDED_CARD_CAST
    handler_id: str = "choice.cast.discarded-card-after-source-counters.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 118.9d", "CR 400.7j", "CR 601.2", "CR 608.2g", "CR 608.2h", "CR 701.9")
    capability_dependencies: tuple[str, ...] = (DISCARDED_CARD_CAST_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("player", "schema_version", _HAND_FIELD, _LABEL_FIELD, _DISCARD_FIELD, _OPTIONS_FIELD)
    private_data: tuple[str, ...] = ("hand incarnations",)
    projected_fields: tuple[str, ...] = ("prompt", "cards", "card", "cast_options", "legal_actions.choice_schema")
    mutation_path: tuple[str, ...] = ("MoveObjectsSimultaneouslyIntent", "DiscardedCardCastIntent", "commit_resolving_cast")
    replay_fixture: str = "discarded-card-cast-after-source-counters"
    test_modules: tuple[str, ...] = ("tests.test_discarded_card_cast",)

    def prepare(self, effect, context):
        if context.actor != context.stack_controller:
            raise SemanticChoiceError("Discard casting belongs to the resolving trigger controller")
        try:
            if _DISCARD_FIELD in effect:
                return _prepare_cast(effect, context)
            return _prepare_discard(effect, context)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc

    def complete(self, continuation, response, query):
        try:
            if _DISCARD_FIELD in continuation.effect:
                return _complete_cast(continuation, response, query)
            return _complete_discard(continuation, response, query)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
