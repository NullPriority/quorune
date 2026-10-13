from __future__ import annotations

"""Closed fixed-count Surveil choice preparation and completion."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..replacement.immutable import FrozenMap
from ..rules.library_surveillance import (
    SurveilArrangement,
    SurveilError,
    SurveilObjectIdentity,
)
from ..semantic_runtime.intents import (
    RevealLibraryCardsIntent,
    SurveilLibraryIntent,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    LibraryPartitionChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_LIBRARY_ZONE = "library"


@dataclass(frozen=True, slots=True)
class SurveilChoiceHandler:
    operation: str = "surveil"
    handler_id: str = "choice.library.surveil.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 701.25",
        "CR 701.25a",
        "CR 701.25b",
    )
    capability_dependencies: tuple[str, ...] = (
        "library.surveil.fixed_controller",
    )
    continuation_fields: tuple[str, ...] = (
        "count",
        "player",
        "_choice_actor",
        "_looked_objects",
        "_stack_label",
    )
    private_data: tuple[str, ...] = (
        "actor library top",
        "looked-at object identities",
    )
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "RevealLibraryCardsIntent",
        "SurveilLibraryIntent",
        "ZoneTransitionOwner.move_cards_simultaneously",
    )
    replay_fixture: str = "semantic-choice-surveil-partition"
    test_modules: tuple[str, ...] = ("tests.test_surveil_rules",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        if set(effect) != {"op", "player", "count"}:
            raise SemanticChoiceError("Surveil effects have a closed field set")
        if (
            effect["player"] != context.actor
            or context.stack_controller != context.actor
        ):
            raise SemanticChoiceError(
                "Fixed Surveil can affect only the resolving controller"
            )
        count = effect["count"]
        if type(count) is not int or count < 0:
            raise SemanticChoiceError(
                "Surveil count must be an exact nonnegative integer"
            )
        if count == 0:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="Surveil 0 creates no Surveil event"),
            )
        refs = context.query.library_refs(context.actor, top_first=True)[:count]
        if not refs:
            empty = SurveilArrangement(
                looked=(),
                top_top_first=(),
                graveyard_refs=(),
            )
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(
                    reason="Surveil completed with no cards in the library"
                ),
                preparation_intents=(
                    SurveilLibraryIntent(
                        actor=context.actor,
                        player=context.actor,
                        arrangement=empty,
                        requested_count=count,
                        reason=context.stack_label,
                    ),
                ),
            )
        rows = []
        identities = []
        for ref in refs:
            row = context.query.object(ref, zones=(_LIBRARY_ZONE,))
            if row is None:
                raise SemanticChoiceError(
                    "A looked-at card is absent from the actor query"
                )
            rows.append(row)
            try:
                identities.append(
                    SurveilObjectIdentity(
                        object_id=row.object_id,
                        logical_object_id=row.logical_object_id,
                        ref=row.ref,
                    )
                )
            except SurveilError as exc:
                raise SemanticChoiceError(str(exc)) from exc
        continuation = FrozenMap(
            {
                **dict(effect),
                "_choice_actor": context.actor,
                "_looked_objects": [
                    identity.to_dict() for identity in identities
                ],
                "_stack_label": context.stack_label,
            }
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    "Put any number of the looked-at cards into your "
                    "graveyard and order the rest on top of your library."
                ),
                choice=LibraryPartitionChoice(
                    field_name="cards",
                    legal_refs=refs,
                    partitions=FrozenMap(
                        {
                            "top": {
                                "order": "top_to_bottom",
                                "label": "Top of library",
                            },
                            "graveyard": {
                                "order": "graveyard_top_to_bottom",
                                "label": "Graveyard",
                            },
                        }
                    ),
                    legacy_destination=None,
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "objects": [
                            {"id": row.ref, "name": row.printed_name}
                            for row in rows
                        ],
                    }
                ),
            ),
            continuation_effect=continuation,
            preparation_intents=(
                RevealLibraryCardsIntent(
                    actor=context.stack_controller,
                    player=context.actor,
                    viewer=context.actor,
                    refs_top_first=refs,
                    reason=context.stack_label,
                ),
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        del query
        raw = continuation.effect.get("_looked_objects", ())
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise SemanticChoiceError(
                "Surveil continuation identities are malformed"
            )
        try:
            looked = tuple(
                SurveilObjectIdentity.from_dict(value) for value in raw
            )
            arrangement = SurveilArrangement.from_response(looked, response)
        except SurveilError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        actor = str(continuation.effect["_choice_actor"])
        return SemanticChoiceCompletion(
            intents=(
                SurveilLibraryIntent(
                    actor=actor,
                    player=actor,
                    arrangement=arrangement,
                    requested_count=int(continuation.effect["count"]),
                    reason=str(continuation.effect["_stack_label"]),
                ),
            )
        )


SURVEIL_CHOICE_HANDLERS = (SurveilChoiceHandler(),)


__all__ = ["SURVEIL_CHOICE_HANDLERS", "SurveilChoiceHandler"]
