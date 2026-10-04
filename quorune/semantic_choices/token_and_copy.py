from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import (
    CopyControlledTokensIntent,
    CreateTokenIntent,
    PlaceCountersIntent,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ObjectChoice,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)

_FABRICATE_OPERATION = "".join(("fabri", "cate"))


@dataclass(frozen=True, slots=True)
class FabricateChoiceHandler:
    operation: str = _FABRICATE_OPERATION
    handler_id: str = "choice.token.fabricate.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 702.123a", "CR 603.6a")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "amount",
        "_choice_actor",
        "_source_ref",
        "_legal_values",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "options",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "PlaceCountersIntent or CreateTokenIntent",
    )
    replay_fixture: str = "semantic-choice-fabricate"
    test_modules: tuple[str, ...] = ("tests.test_exact_artifact_engines",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        source = (
            context.query.object(context.source_ref or "")
            if context.source_ref
            else None
        )
        legal_values = ("counter", "token") if (
            source is not None and source.zone == "battlefield"
        ) else ("token",)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    "Choose whether to put +1/+1 counter(s) on the source "
                    "or create Servo token(s)."
                ),
                choice=ScalarChoice(
                    field_name="choice",
                    legal_values=legal_values,
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "options": [
                            {
                                "id": value,
                                "label": (
                                    "Put +1/+1 counters on this creature"
                                    if value == "counter"
                                    else "Create Servo tokens"
                                ),
                            }
                            for value in legal_values
                        ],
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_source_ref": context.source_ref,
                    "_legal_values": legal_values,
                    "_stack_label": context.stack_label,
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
        choice = str(response.get("choice") or "")
        legal = {str(value) for value in effect.get("_legal_values", ())}
        if choice not in legal:
            raise SemanticChoiceError(
                "Choose an authoritative fabricate option"
            )
        actor = str(effect["_choice_actor"])
        amount = max(0, int(effect.get("amount", 1)))
        label = str(effect["_stack_label"])
        if choice == "counter":
            source_ref = str(effect.get("_source_ref") or "")
            source = query.object(source_ref, zones=("battlefield",))
            if source is None:
                raise SemanticChoiceError(
                    "The fabricate source is no longer on the battlefield"
                )
            intent: Any = PlaceCountersIntent(
                actor=actor,
                object_refs=(source_ref,),
                counter_name="+1/+1",
                amount=amount,
                reason=label,
                source_ref=source_ref,
            )
        else:
            intent = CreateTokenIntent(
                actor=actor,
                controller=actor,
                name="Servo",
                quantity=amount,
                characteristics=FrozenMap(
                    {
                        "type_line": "Artifact Creature — Servo",
                        "power": "1",
                        "toughness": "1",
                        "colors": [],
                    }
                ),
                reason=label,
            )
        return SemanticChoiceCompletion(intents=(intent,))


@dataclass(frozen=True, slots=True)
class TokenCopyChoiceHandler:
    operation: str
    handler_id: str
    mode: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 701.30", "CR 707.2")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "_choice_actor",
        "_legal_refs",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "options",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "CreateTokenIntent or CopyControlledTokensIntent",
    )
    replay_fixture: str = "semantic-choice-token-copy"
    test_modules: tuple[str, ...] = ("tests.test_exact_artifact_engines",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        options = tuple(
            row
            for row in context.query.objects(
                zones=("battlefield",),
                controller=context.actor,
            )
            if row.token and (self.mode == "all" or "creature" in row.types)
        )
        if not options:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="no eligible token"),
            )
        refs = tuple(row.ref for row in options)
        prompt = (
            "Choose a creature token to populate."
            if self.mode == "populate"
            else "Choose a token for every other token to copy."
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=prompt,
                choice=ObjectChoice(
                    field_name="card",
                    legal_refs=refs,
                    zones=("battlefield",),
                    controller_relation="actor",
                    predicates=FrozenMap({"token": True}),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "options": refs,
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_legal_refs": refs,
                    "_stack_label": context.stack_label,
                    **({"_object_identities": {row.ref:row.logical_object_id for row in options}}
                       if self.operation == "populate" else {}),
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
        selected = str(response.get("card") or "")
        legal = {str(value) for value in effect.get("_legal_refs", ())}
        if selected not in legal:
            raise SemanticChoiceError(
                "Selected token is not an authoritative copy option"
            )
        actor = str(effect["_choice_actor"])
        row = query.object(selected, zones=("battlefield",))
        if row is None or row.controller != actor or not row.token:
            raise SemanticChoiceError("Token-copy choice requires a token")
        if self.operation == "populate" and row.logical_object_id != effect.get("_object_identities", {}).get(selected):
            raise SemanticChoiceError("Populate token incarnation changed")
        label = str(effect["_stack_label"])
        if self.mode == "populate":
            if "creature" not in row.types:
                raise SemanticChoiceError("Populate requires a creature token")
            intent: Any = CreateTokenIntent(
                actor=actor,
                controller=actor,
                name=row.printed_name,
                quantity=1,
                copy_of=row.ref,
                temporary_keywords=("Haste",) if self.operation == "populate_with_haste" else (),
                sacrifice_at_end_step=self.operation == "populate_with_haste",
                reason=label,
            )
        else:
            intent = CopyControlledTokensIntent(
                actor=actor,
                controller=actor,
                chosen_token_ref=row.ref,
                source_stack_ref=continuation.stack_ref,
                reason=label,
            )
        return SemanticChoiceCompletion(intents=(intent,))


TOKEN_AND_COPY_CHOICE_HANDLERS = (
    FabricateChoiceHandler(),
    TokenCopyChoiceHandler(
        operation="populate",
        handler_id="choice.token.populate.v1",
        mode="populate",
        capability_dependencies=("token.creation.fixed_copy",),
        rule_references=("CR 701.36a", "CR 707.2"),
        continuation_fields=("_choice_actor", "_legal_refs", "_stack_label", "_object_identities"),
        test_modules=("tests.test_fixed_token_creation_effects",),
    ),
    TokenCopyChoiceHandler(
        operation="populate_with_haste",
        handler_id="choice.token.populate-haste.v1",
        mode="populate",
    ),
    TokenCopyChoiceHandler(
        operation="copy_all_tokens",
        handler_id="choice.token.copy-all.v1",
        mode="all",
    ),
)
