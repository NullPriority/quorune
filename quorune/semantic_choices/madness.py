from __future__ import annotations

"""Typed cast-or-graveyard choice for a discarded Madness card."""

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from ..cast_lifecycles import (
    FixedCastLifecycleError,
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
)
from ..madness import MADNESS_CAPABILITY_ID, MADNESS_CHOICE_OPERATION
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime.context import SemanticSourceContext
from ..semantic_runtime.intents import MadnessChoiceIntent
from ..util import stable_json
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _options_fingerprint(options: tuple[Mapping[str, Any], ...]) -> str:
    return hashlib.sha256(
        stable_json([thaw_value(option) for option in options]).encode()
    ).hexdigest()


def _spec(value: Any) -> FixedCastLifecycleSpec:
    try:
        spec = FixedCastLifecycleSpec.from_dict(value)
    except (FixedCastLifecycleError, TypeError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if spec.kind is not FixedCastLifecycleKind.MADNESS:
        raise SemanticChoiceError("Madness choice lifecycle changed")
    return spec


@dataclass(frozen=True, slots=True)
class MadnessCastChoiceHandler:
    operation: str = MADNESS_CHOICE_OPERATION
    handler_id: str = "choice.madness.cast-or-graveyard.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 400.7k", "CR 702.35a", "CR 702.35b")
    capability_dependencies: tuple[str, ...] = (MADNESS_CAPABILITY_ID,)
    continuation_fields: tuple[str, ...] = (
        "madness",
        "_choice_actor",
        "_source_ref",
        "_source_object_id",
        "_source_logical_object_id",
        "_options_fingerprint",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "card",
        "cast_options",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "MadnessChoiceIntent",
        "CommanderEngine.madness_choice_intent",
    )
    replay_fixture: str = "madness-fixed-mana-choice"
    test_modules: tuple[str, ...] = ("tests.test_madness_rules",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        if set(effect) != {"op", "madness"} or effect.get("op") != self.operation:
            raise SemanticChoiceError("Madness choice effect is malformed")
        _spec(effect["madness"])
        if (
            context.source_ref is None
            or context.source_object_id is None
            or context.source_logical_object_id is None
        ):
            raise SemanticChoiceError(
                "Madness choice requires its source incarnation"
            )
        options = tuple(context.query.authorized_cast_options())
        choices = ("cast", "decline") if options else ("decline",)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Cast the discarded card for its Madness cost?",
                choice=ScalarChoice(
                    field_name="choice",
                    legal_values=choices,
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "card": context.source_ref,
                        "cast_options": list(options),
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_source_ref": context.source_ref,
                    "_source_object_id": context.source_object_id,
                    "_source_logical_object_id": (
                        context.source_logical_object_id
                    ),
                    "_options_fingerprint": _options_fingerprint(options),
                }
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        spec = _spec(effect.get("madness"))
        options = tuple(query.authorized_cast_options())
        if _options_fingerprint(options) != effect.get("_options_fingerprint"):
            raise SemanticChoiceError("Madness cast options changed")
        choice = str(response.get("choice") or "")
        legal = {"decline", "cast"} if options else {"decline"}
        if choice not in legal:
            raise SemanticChoiceError("Choose a current Madness action")
        cast_response = dict(response.get("cast") or {})
        cast_response.update(
            {
                key: value
                for key, value in response.items()
                if key not in {"action", "cast", "choice"}
            }
        )
        return SemanticChoiceCompletion(
            intents=(
                MadnessChoiceIntent(
                    actor=str(effect["_choice_actor"]),
                    source=SemanticSourceContext(
                        stack_ref=continuation.stack_ref,
                        object_id=str(effect["_source_object_id"]),
                        logical_object_id=str(
                            effect["_source_logical_object_id"]
                        ),
                        card_ref=str(effect["_source_ref"]),
                    ),
                    lifecycle=spec,
                    reason="Madness cast choice",
                    choice=choice,
                    response=FrozenMap(cast_response),
                    options_fingerprint=str(
                        effect["_options_fingerprint"]
                    ),
                ),
            ),
        )


MADNESS_CHOICE_HANDLERS = (MadnessCastChoiceHandler(),)


__all__ = ["MADNESS_CHOICE_HANDLERS", "MadnessCastChoiceHandler"]
