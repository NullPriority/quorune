from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..object_query import object_matches_query, query_objects
from ..semantic_runtime.intents import (
    LifeChangeIntent,
    RecordZoneMoveIntent,
    ZoneMoveIntent,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ObjectChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


@dataclass(frozen=True, slots=True)
class PutCardFromHandHandler:
    operation: str
    handler_id: str
    required_type: str
    prompt: str
    event_code: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 400.7", "CR 608.2d")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "player",
        "cave_life",
        "_choice_actor",
        "_legal_refs",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ("actor hand",)
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "ZoneMoveIntent",
        "CommanderEngine.move_object_intent",
    )
    replay_fixture: str = "semantic-choice-put-from-hand"
    test_modules: tuple[str, ...] = (
        "tests.test_semantic_choice_characterization",
        "tests.test_exact_zimone_closure",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        options = tuple(
            row
            for row in context.query.objects(
                zones=("hand",),
                owner=context.actor,
            )
            if self.required_type in row.types
        )
        if not options:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(
                    reason=f"no {self.required_type} card in hand"
                ),
            )
        legal_refs = tuple(row.ref for row in options)
        continuation_effect = FrozenMap(
            {
                **dict(effect),
                "_choice_actor": context.actor,
                "_legal_refs": legal_refs,
                "_stack_label": context.stack_label,
            }
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=str(effect.get("prompt") or self.prompt),
                choice=ObjectChoice(
                    field_name="card",
                    legal_refs=legal_refs,
                    zones=("hand",),
                    minimum=0,
                    maximum=1,
                    optional=True,
                    visibility="actor_private",
                    owner_relation="actor",
                    predicates=FrozenMap({"types": [self.required_type]}),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "objects": [
                            {"id": row.ref, "name": row.printed_name}
                            for row in options
                        ],
                    }
                ),
            ),
            continuation_effect=continuation_effect,
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        selected = str(response.get("card") or "")
        legal = {
            str(value)
            for value in continuation.effect.get("_legal_refs", ())
        }
        if selected and selected not in legal:
            raise SemanticChoiceError(
                f"Selected card is not an authoritative {self.required_type} option"
            )
        if not selected:
            return SemanticChoiceCompletion()
        actor = str(continuation.effect["_choice_actor"])
        row = query.object(selected, zones=("hand",))
        if row is None or row.owner != actor or self.required_type not in row.types:
            raise SemanticChoiceError(
                f"Selected object is no longer a {self.required_type} card in hand"
            )
        label = str(continuation.effect["_stack_label"])
        move = ZoneMoveIntent(
            actor=actor,
            object_ref=selected,
            expected_zones=("hand",),
            destination="battlefield",
            reason=label,
            required_types=(self.required_type,),
            owned_only=True,
            new_controller=actor,
            tapped_policy=(
                "land_entry" if self.required_type == "land" else "preserve"
            ),
        )
        intents: list[Any] = [move]
        cave_life = int(continuation.effect.get("cave_life", 0))
        if self.required_type == "land" and "cave" in row.subtypes and cave_life:
            intents.append(
                LifeChangeIntent(
                    actor=actor,
                    player=actor,
                    amount=cave_life,
                    reason=label,
                )
            )
        message = (
            f"{actor} put {selected} onto the battlefield."
            if self.required_type == "land"
            else f"{actor} put {selected} onto the battlefield from hand."
        )
        details: dict[str, Any] = {
            "card": selected,
            "source": continuation.stack_ref,
        }
        if self.required_type == "land":
            details["include_tapped_state"] = True
        intents.append(
            RecordZoneMoveIntent(
                actor=actor,
                object_ref=selected,
                event_code=self.event_code,
                message=message,
                details=FrozenMap(details),
                changed_player=actor,
            )
        )
        return SemanticChoiceCompletion(intents=tuple(intents))


@dataclass(frozen=True, slots=True)
class PutTypedCardFromHandHandler:
    operation: str = "put_card_from_hand"
    handler_id: str = "choice.object.put-typed-card-from-hand.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 400.7",
        "CR 608.2c",
        "CR 608.2d",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.move.fixed_private_hand_choice",
        "zone.change.destination_replacement",
    )
    continuation_fields: tuple[str, ...] = (
        "player",
        "query",
        "tapped",
        "_choice_actor",
        "_choice_query",
        "_legal_refs",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ("actor hand", "eligible card identities")
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "ZoneMoveIntent",
        "CommanderEngine.move_object_intent",
    )
    replay_fixture: str = "fixed-private-hand-entry"
    test_modules: tuple[str, ...] = (
        "tests.test_fixed_public_zone_moves",
    )

    @staticmethod
    def _query(
        effect: Mapping[str, Any],
        *,
        actor: str | None = None,
    ) -> ObjectQuerySpec:
        allowed = {"op", "player", "query", "tapped", "prompt"}
        if (
            set(effect) - allowed
            or set(effect).intersection({"op", "player", "query", "tapped"})
            != {"op", "player", "query", "tapped"}
            or effect.get("op") != "put_card_from_hand"
            or effect.get("player")
            != (actor if actor is not None else "$controller")
            or type(effect.get("tapped")) is not bool
        ):
            raise SemanticChoiceError(
                "Private hand-entry effect has an invalid shape"
            )
        try:
            query = ObjectQuerySpec.from_dict(effect["query"])
        except (KeyError, TypeError, ObjectQueryError) as exc:
            raise SemanticChoiceError(
                "Private hand-entry query is malformed"
            ) from exc
        if (
            query.zones != ("hand",)
            or query.owner is not None
            or query.controller is not None
            or query.include_phased_out
            or query.known_to_actor is not None
            or query.exclude_ref is not None
            or query.state_predicate is not None
        ):
            raise SemanticChoiceError(
                "Private hand-entry query is outside the closed family"
            )
        return query

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        query = self._query(effect, actor=context.actor)
        options = tuple(
            sorted(
                query_objects(
                    context.query.objects(
                        zones=("hand",),
                        owner=context.actor,
                    ),
                    query,
                ),
                key=lambda row: row.ref,
            )
        )
        if not options:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="no matching card in hand"),
            )
        legal_refs = tuple(row.ref for row in options)
        continuation_effect = FrozenMap(
            {
                **dict(effect),
                "_choice_actor": context.actor,
                "_choice_query": query.canonical_dict(),
                "_legal_refs": legal_refs,
                "_stack_label": context.stack_label,
            }
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=str(
                    effect.get("prompt")
                    or "You may put a matching card from your hand onto the battlefield."
                ),
                choice=ObjectChoice(
                    field_name="card",
                    legal_refs=legal_refs,
                    zones=("hand",),
                    minimum=0,
                    maximum=1,
                    optional=True,
                    visibility="actor_private",
                    owner_relation="actor",
                    predicates=FrozenMap(query.canonical_dict()),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "objects": [
                            {"id": row.ref, "name": row.printed_name}
                            for row in options
                        ],
                    }
                ),
            ),
            continuation_effect=continuation_effect,
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        selected = str(response.get("card") or "")
        legal = {
            str(value) for value in continuation.effect.get("_legal_refs", ())
        }
        if selected and selected not in legal:
            raise SemanticChoiceError(
                "Selected card is not an authoritative private hand-entry option"
            )
        if not selected:
            return SemanticChoiceCompletion()
        actor = str(continuation.effect["_choice_actor"])
        try:
            predicate = ObjectQuerySpec.from_dict(
                continuation.effect["_choice_query"]
            )
        except (KeyError, TypeError, ObjectQueryError) as exc:
            raise SemanticChoiceError(
                "Private hand-entry continuation query is malformed"
            ) from exc
        row = query.object(selected, zones=("hand",))
        if (
            row is None
            or row.owner != actor
            or not object_matches_query(row, predicate)
        ):
            raise SemanticChoiceError(
                "Selected object no longer satisfies the private hand-entry query"
            )
        label = str(continuation.effect["_stack_label"])
        tapped = continuation.effect.get("tapped")
        if type(tapped) is not bool:
            raise SemanticChoiceError(
                "Private hand-entry tapped policy is malformed"
            )
        land_only = predicate.types_all == ("land",)
        move = ZoneMoveIntent(
            actor=actor,
            object_ref=selected,
            expected_zones=("hand",),
            destination="battlefield",
            reason=label,
            required_types=predicate.types_all,
            owned_only=True,
            new_controller=actor,
            tapped_policy=(
                "tapped" if tapped else "land_entry" if land_only else "untapped"
            ),
        )
        return SemanticChoiceCompletion(
            intents=(
                move,
                RecordZoneMoveIntent(
                    actor=actor,
                    object_ref=selected,
                    event_code="card.put_from_hand",
                    message=(
                        f"{actor} put {selected} onto the battlefield from hand."
                    ),
                    details=FrozenMap(
                        {
                            "card": selected,
                            "source": continuation.stack_ref,
                            "include_tapped_state": True,
                        }
                    ),
                    changed_player=actor,
                ),
            )
        )


OBJECT_SELECTION_HANDLERS = (
    PutTypedCardFromHandHandler(),
    PutCardFromHandHandler(
        operation="put_land_from_hand",
        handler_id="choice.object.put-land-from-hand.v1",
        required_type="land",
        prompt="You may put a land card from your hand onto the battlefield.",
        event_code="land.put",
    ),
    PutCardFromHandHandler(
        operation="put_artifact_from_hand",
        handler_id="choice.object.put-artifact-from-hand.v1",
        required_type="artifact",
        prompt=(
            "You may put an artifact card from your hand onto the battlefield."
        ),
        event_code="artifact.put",
    ),
)
