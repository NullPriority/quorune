"""A private, replayable play-or-decline offer for the exact Hideaway exile link."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..discover_model import DiscoverCastIntent, ExileCastProgress, discover_options_fingerprint
from ..hideaway_model import HIDEAWAY_PLAY_CAPABILITY, HIDEAWAY_PLAY_OPERATION, validate_hideaway_play_effect
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from .fixed_top_exile import _land_play_intent
from .model import (
    AutoContinue, CastOrDeclineChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_CONTINUATION_FIELDS = frozenset({"_qualified", "_stack_ref", "_options_fingerprint"})


def hideaway_offer_instruction(effect, *, actor, stack_ref=None):
    ordinary = {key: value for key, value in effect.items() if key not in _CONTINUATION_FIELDS}
    try:
        validate_hideaway_play_effect(ordinary, player=actor)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if ("_qualified" in effect and effect["_qualified"] is not True
            or stack_ref is not None and effect.get("_stack_ref", stack_ref) != stack_ref
            or ("_stack_ref" in effect) != ("_options_fingerprint" in effect)):
        raise SemanticChoiceError("Hideaway lost its qualified resolving offer")
    return ordinary


def _submitted_play(response, options):
    choice = response.get("choice")
    raw = response.get("cast")
    submitted = {key: value for key, value in response.items()
                 if key not in {"action", "action_id", "choice", "cast", "choice_schema"}}
    if choice == "decline" and raw is None and not submitted:
        return None, None
    if choice != "play" or raw is not None and not isinstance(raw, Mapping) or (raw or {}).keys() & submitted.keys():
        raise SemanticChoiceError("Choose one current Hideaway play or decline the remaining cards")
    submission = {**(raw or {}), **submitted}
    chosen = tuple(row for row in options if submission.get("cost_option") in (None, row["id"]))
    if len(chosen) != 1:
        raise SemanticChoiceError("Select one current Hideaway card, face and payment")
    if submission.get("card") not in (None, chosen[0]["card"]):
        raise SemanticChoiceError("The submitted card is outside the selected Hideaway option")
    return chosen[0], submission


@dataclass(frozen=True, slots=True)
class HideawayPlayChoiceHandler:
    operation: str = HIDEAWAY_PLAY_OPERATION
    handler_id: str = "choice.library.hideaway-linked-play.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("305.2", "305.3", "406.3a", "607.2a", "607.3", "608.2g", "608.2h")
    capability_dependencies: tuple[str, ...] = (HIDEAWAY_PLAY_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "minimum_power",
                                          "_qualified", "_stack_ref", "_options_fingerprint")
    private_data: tuple[str, ...] = ("current linked face-down exile identities",)
    projected_fields: tuple[str, ...] = ("prompt", "cast_options", "legal_actions.choice_schema.legal_values")
    mutation_path: tuple[str, ...] = ("DiscoverCastIntent", "ResolvingExileLandPlayIntent")
    replay_fixture: str = "hideaway-linked-play"
    test_modules: tuple[str, ...] = ("tests.test_hideaway_linked_play",)

    def prepare(self, effect, context):
        if context.actor != context.stack_controller or "_stack_ref" in effect:
            raise SemanticChoiceError("The resolving Hideaway controller owns its play offer")
        ordinary = hideaway_offer_instruction(effect, actor=context.actor)
        options = tuple(context.query.authorized_cast_options())
        if not options:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="There is no current legal Hideaway play."),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Play a linked Hideaway card without paying its mana cost, or decline.",
                choice=CastOrDeclineChoice(options, selection="play"),
                public_context=FrozenMap({
                    "stack": context.stack_ref, "operation": self.operation, "cast_options": list(options),
                }),
            ),
            continuation_effect=FrozenMap({
                **ordinary, "_qualified": True, "_stack_ref": context.stack_ref,
                "_options_fingerprint": discover_options_fingerprint(options),
            }),
        )

    def complete(self, continuation, response, query):
        saved = continuation.effect
        actor = saved.get("player")
        ordinary = hideaway_offer_instruction(saved, actor=actor, stack_ref=continuation.stack_ref)
        options = tuple(query.authorized_cast_options())
        if (saved.get("_qualified") is not True or actor not in query.active_seats
                or discover_options_fingerprint(options) != saved.get("_options_fingerprint")):
            raise SemanticChoiceError("The exact Hideaway offer is no longer available")
        chosen, submission = _submitted_play(response, options)
        if chosen is None:
            return SemanticChoiceCompletion()
        card = query.object(chosen["card"], zones=("exile",))
        if card is None or not card.known_to_actor or card.token:
            raise SemanticChoiceError("The chosen Hideaway card is no longer available")
        identity = LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref)
        if chosen.get("kind") == "land":
            intent = _land_play_intent(actor, continuation.stack_ref, identity, chosen, submission)
        else:
            matching = tuple(row for row in options if row["card"] == card.ref and row.get("kind") != "land")
            progress = ExileCastProgress(None, (identity,), card.ref, card.owner, "free")
            intent = DiscoverCastIntent(
                actor, continuation.stack_ref, progress, discover_options_fingerprint(matching),
                FrozenMap({**submission, "cost_option": chosen["id"]}),
            )
        return SemanticChoiceCompletion(
            intents=(intent,), prepend_effects=(FrozenMap({**ordinary, "_qualified": True}),),
        )
