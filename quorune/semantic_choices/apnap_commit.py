from __future__ import annotations

"""Typed commit bridge for collected APNAP object choices."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..library_order_model import HAND_TOP_APNAP_EVENT
from ..replacement.immutable import FrozenMap
from ..apnap_object_payment import DECLINED_LIFE_LOSS
from ..rules.counted_sacrifice_tokens import COUNTED_SACRIFICE_TOKEN_FIELD
from ..semantic_runtime.counted_sacrifice_token_intent import CountedSacrificeQuantityIntent, CountedSacrificeTokenIntent
from ..rules.any_number_sacrifice import ANY_NUMBER_SACRIFICE_FIELD
from ..power_sacrifice_model import GREATEST_POWER_SACRIFICE_LIFE_RESULT, GreatestPowerSacrificeLifeResult
from ..semantic_runtime.targeted_power_sacrifice_life_intent import TargetedPowerSacrificeLifeIntent
from ..semantic_runtime.life_batch_intent import LifeLossBatchIntent
from ..semantic_runtime.intents import (
    MoveObjectsSimultaneouslyIntent,
    RecordChoiceIntent,
)
from ..zone_trigger_events import ZoneTransitionKind
from ..discard_effect_origin import DiscardEffectOrigin
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    IntentResultAmount,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
)


APNAP_OBJECT_COMMIT_OPERATION = "commit_apnap_object_choices"
APNAP_OBJECT_COMMIT_HANDLER_ID = "choice.apnap.object-commit.v1"
_FIELDS = {
    "op",
    "actor",
    "object_refs",
    "expected_zones",
    "destination",
    "reason",
    "event_code",
    "message",
}
_LOSS_FIELDS = {DECLINED_LIFE_LOSS, 'declining_players'}
_COUNTED_MOVES = {
    COUNTED_SACRIFICE_TOKEN_FIELD: CountedSacrificeTokenIntent,
    ANY_NUMBER_SACRIFICE_FIELD: CountedSacrificeQuantityIntent,
}


def _sacrifice_power_move(effect, *, actor, refs, zones, destination, reason, event_code):
    if event_code != "choice.sacrifice":
        raise SemanticChoiceError("The selected life result requires a creature sacrifice")
    try:
        result = GreatestPowerSacrificeLifeResult.from_dict(effect[GREATEST_POWER_SACRIFICE_LIFE_RESULT])
        return TargetedPowerSacrificeLifeIntent(
            actor=actor, object_refs=refs, expected_zones=zones, destination=destination,
            reason=reason, transition_kind=ZoneTransitionKind.SACRIFICE, result=result,
        )
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _intents(
    effect: Mapping[str, Any], *, source_ref: str | None = None, discard_effect_origin: DiscardEffectOrigin | None = None,
) -> tuple[Any, ...]:
    if not isinstance(effect, Mapping) or set(effect) not in (
        _FIELDS, _FIELDS | _LOSS_FIELDS, _FIELDS | {GREATEST_POWER_SACRIFICE_LIFE_RESULT},
        *(_FIELDS | {key} for key in _COUNTED_MOVES),
    ):
        raise SemanticChoiceError("APNAP object commit fields are malformed")
    if effect["op"] != APNAP_OBJECT_COMMIT_OPERATION:
        raise SemanticChoiceError("APNAP object commit operation changed")
    try:
        refs = tuple(effect["object_refs"])
        zones = tuple(effect["expected_zones"])
    except TypeError as exc:
        raise SemanticChoiceError(
            "APNAP object commit identities must be arrays"
        ) from exc
    actor = effect["actor"]
    destination = effect["destination"]
    reason = effect["reason"]
    event_code = effect["event_code"]
    message = effect["message"]
    hand_top = event_code == HAND_TOP_APNAP_EVENT
    if (
        any(type(value) is not str or not value for value in refs)
        or len(refs) != len(set(refs))
        or not zones
        or any(type(value) is not str or not value for value in zones)
        or len(zones) != len(set(zones))
        or any(
            type(value) is not str or not value
            for value in (actor, destination, reason, event_code, message)
        )
        or (
            (zones != ("hand",) or destination != "library" or DECLINED_LIFE_LOSS in effect)
            if hand_top else destination not in {"exile", "graveyard", "hand"}
        )
        or event_code not in {
            "choice.discard",
            "choice.exile",
            "choice.sacrifice",
            "choice.return_owner_hand",
            HAND_TOP_APNAP_EVENT,
        }
    ):
        raise SemanticChoiceError("APNAP object commit is malformed")
    move = (
        (
            MoveObjectsSimultaneouslyIntent(
                actor=actor,
                object_refs=refs,
                expected_zones=zones,
                destination=destination,
                reason=reason,
                transition_kind=(
                    ZoneTransitionKind.DISCARD
                    if event_code == "choice.discard"
                    else ZoneTransitionKind.SACRIFICE
                    if event_code == "choice.sacrifice"
                    else ZoneTransitionKind.ORDINARY
                ),
                discard_effect_origin=discard_effect_origin,
            ),
        )
        if refs
        else ()
    )
    for field, intent_type in _COUNTED_MOVES.items():
        if field not in effect:
            continue
        if event_code != "choice.sacrifice":
            raise SemanticChoiceError("Counted results require a committed sacrifice")
        try:
            move = (intent_type(
                actor=actor, object_refs=refs, expected_zones=zones, destination=destination,
                reason=reason, transition_kind=ZoneTransitionKind.SACRIFICE, payload=effect[field],
            ),)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
    if GREATEST_POWER_SACRIFICE_LIFE_RESULT in effect:
        move = (_sacrifice_power_move(effect, actor=actor, refs=refs, zones=zones,
                                      destination=destination, reason=reason, event_code=event_code),)
    if hand_top:
        return move
    loss = ()
    if DECLINED_LIFE_LOSS in effect:
        players = effect['declining_players']
        if ((zones, destination, event_code) not in ((('hand',), 'graveyard', 'choice.discard'),
                                                    (('battlefield',), 'graveyard', 'choice.sacrifice'))
                or not isinstance(players, (list, tuple))
                or any(type(seat) is not str or not seat for seat in players)):
            raise SemanticChoiceError('Declined APNAP payment needs its sealed player set')
        try:
            loss = (LifeLossBatchIntent(actor, tuple(players), effect[DECLINED_LIFE_LOSS],
                                        'Declined APNAP object payment', source_ref),)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
    return (
        *move,
        RecordChoiceIntent(
            actor=actor,
            event_code=event_code,
            message=message,
            details=FrozenMap({"objects": list(refs)}),
            importance=2,
            changed_object_refs=refs,
        ),
        *loss,
    )


@dataclass(frozen=True, slots=True)
class ApnapObjectCommitHandler:
    operation: str = APNAP_OBJECT_COMMIT_OPERATION
    handler_id: str = APNAP_OBJECT_COMMIT_HANDLER_ID
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 101.4",
        "CR 608.2c",
        "CR 701.9",
        "CR 701.9a",
        "CR 701.9c",
        "CR 701.21",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.change.destination_replacement",
    )
    continuation_fields: tuple[str, ...] = tuple(sorted(_FIELDS | _LOSS_FIELDS | {GREATEST_POWER_SACRIFICE_LIFE_RESULT, *_COUNTED_MOVES}))
    private_data: tuple[str, ...] = ("object_refs",)
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = (
        "MoveObjectsSimultaneouslyIntent",
        "ZoneTransitionOwner.move_cards_simultaneously",
        "LifeLossBatchIntent",
    )
    replay_fixture: str = "apnap-object-choice-commit"
    test_modules: tuple[str, ...] = (
        "tests.test_fixed_affected_player_discards",
        "tests.test_fixed_affected_player_sacrifices",
        "tests.test_fixed_counter_event_triggers",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        intents = _intents(effect, source_ref=context.source_ref,
            discard_effect_origin=getattr(context.query, "discard_effect_origin", None))
        if effect.get("actor") != context.stack_controller:
            raise SemanticChoiceError("APNAP commit controller changed")
        continuation = AutoContinue(reason="APNAP choices are complete")
        if ANY_NUMBER_SACRIFICE_FIELD in effect:
            continuation = AutoContinue(reason="Apply the committed sacrifice quantity.", committed_effects_from_result=0)
        elif COUNTED_SACRIFICE_TOKEN_FIELD in effect:
            continuation = AutoContinue(reason="Create tokens for the committed sacrifices.", committed_effects_from_result=0)
        elif GREATEST_POWER_SACRIFICE_LIFE_RESULT in effect and intents[0].result.draw_then_lose_life:
            continuation = AutoContinue(
                reason="Use the committed sacrifice's power and toughness.",
                committed_effects_from_result=0,
            )
        elif GREATEST_POWER_SACRIFICE_LIFE_RESULT in effect:
            result = intents[0].result
            recipient = result.player if result.sacrificing_player is not None else context.actor
            continuation = AutoContinue(
                reason="Gain life from the committed creature sacrifice.",
                prepend_effects=(FrozenMap({"op": "life", "player": recipient, "delta": 1}),),
                amount_from_result=IntentResultAmount(0, 0, "delta"),
                draw_after_life_from_result=intents[0].result.draw_after_life,
            )
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(effect),
            preparation_intents=intents,
            auto_continue=continuation,
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        raise SemanticChoiceError("APNAP object commit has no direct choice")


APNAP_COMMIT_CHOICE_HANDLERS = (ApnapObjectCommitHandler(),)


__all__ = [
    "APNAP_COMMIT_CHOICE_HANDLERS",
    "APNAP_OBJECT_COMMIT_HANDLER_ID",
    "APNAP_OBJECT_COMMIT_OPERATION",
    "ApnapObjectCommitHandler",
]
