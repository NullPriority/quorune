from __future__ import annotations

"""Closed Scry choice preparation and completion."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..replacement.immutable import FrozenMap
from ..rules.library_scry import ScryArrangement, ScryError
from ..semantic_runtime.intents import RevealLibraryCardsIntent, ScryLibraryIntent
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
class ScryChoiceHandler:
    operation: str = "scry"
    handler_id: str = "choice.library.scry.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 701.22",
        "CR 701.22a",
        "CR 701.22b",
    )
    capability_dependencies: tuple[str, ...] = (
        "library.scry.fixed_controller",
    )
    continuation_fields: tuple[str, ...] = (
        "count",
        "player",
        "_choice_actor",
        "_looked_refs",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ("actor library top",)
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "RevealLibraryCardsIntent",
        "ScryLibraryIntent",
    )
    replay_fixture: str = "semantic-choice-scry-partition"
    test_modules: tuple[str, ...] = ("tests.test_scry_rules",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        if set(effect) != {"op", "player", "count"}:
            raise SemanticChoiceError("Scry effects have a closed field set")
        count = effect["count"]
        if type(count) is not int or count < 0:
            raise SemanticChoiceError(
                "Scry count must be an exact nonnegative integer"
            )
        refs = context.query.library_refs(context.actor, top_first=True)[:count]
        if count == 0:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(
                    reason="Scry 0 creates no Scry event"
                ),
            )
        if not refs:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="Positive Scry completes with an empty library"),
                preparation_intents=(ScryLibraryIntent(
                    actor=context.actor, player=context.actor,
                    arrangement=ScryArrangement((), (), ()),
                    requested_count=count, reason=context.stack_label,
                ),),
            )
        rows = []
        for ref in refs:
            row = context.query.object(ref, zones=(_LIBRARY_ZONE,))
            if row is None:
                raise SemanticChoiceError(
                    "A looked-at card is absent from the actor query"
                )
            rows.append(row)
        continuation = FrozenMap(
            {
                **dict(effect),
                "_choice_actor": context.actor,
                "_looked_refs": refs,
                "_stack_label": context.stack_label,
            }
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    "Order the looked-at cards on top and on the bottom "
                    "of your library."
                ),
                choice=LibraryPartitionChoice(
                    field_name="cards",
                    legal_refs=refs,
                    visibility="actor_private",
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
        expected = tuple(
            str(value) for value in continuation.effect.get("_looked_refs", ())
        )
        actor = str(continuation.effect["_choice_actor"])
        try:
            arrangement = ScryArrangement.from_response(expected, response)
        except ScryError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoiceCompletion(
            intents=(
                ScryLibraryIntent(
                    actor=actor,
                    player=actor,
                    arrangement=arrangement,
                    requested_count=continuation.effect["count"],
                    reason=str(continuation.effect["_stack_label"]),
                ),
            )
        )


SCRY_CHOICE_HANDLERS = (ScryChoiceHandler(),)


__all__ = ["SCRY_CHOICE_HANDLERS", "ScryChoiceHandler"]
