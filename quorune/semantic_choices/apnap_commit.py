from __future__ import annotations

"""Typed commit bridge for collected APNAP object choices."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import (
    MoveObjectsSimultaneouslyIntent,
    RecordChoiceIntent,
)
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
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


def _intents(effect: Mapping[str, Any]) -> tuple[Any, ...]:
    if not isinstance(effect, Mapping) or set(effect) != _FIELDS:
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
        or destination not in {"exile", "graveyard", "hand"}
        or event_code not in {
            "choice.discard",
            "choice.exile",
            "choice.sacrifice",
            "choice.return_owner_hand",
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
            ),
        )
        if refs
        else ()
    )
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
    continuation_fields: tuple[str, ...] = tuple(sorted(_FIELDS))
    private_data: tuple[str, ...] = ("object_refs",)
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = (
        "MoveObjectsSimultaneouslyIntent",
        "ZoneTransitionOwner.move_cards_simultaneously",
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
        intents = _intents(effect)
        if effect.get("actor") != context.stack_controller:
            raise SemanticChoiceError("APNAP commit controller changed")
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(effect),
            preparation_intents=intents,
            auto_continue=AutoContinue(reason="APNAP choices are complete"),
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
