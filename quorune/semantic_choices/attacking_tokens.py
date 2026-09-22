from __future__ import annotations

"""Public attack-destination choice for tokens entering combat."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import CreateTokenIntent
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    DecisionMapChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


ATTACKING_TOKEN_DESTINATION_OPERATION = "choose_attacking_token_destinations"


def _legal_destinations(
    query: SemanticChoiceQuery,
    actor: str,
) -> tuple[str, ...]:
    opponents = {seat for seat in query.active_seats if seat != actor}
    destinations = set(opponents)
    for row in query.objects(zones=("battlefield",)):
        if "planeswalker" in row.types and row.controller in opponents:
            destinations.add(row.ref)
        elif "battle" in row.types and row.battle_protector in opponents:
            destinations.add(row.ref)
    seat_order = {seat: index for index, seat in enumerate(query.seats)}
    return tuple(
        sorted(
            destinations,
            key=lambda value: (
                0 if value in seat_order else 1,
                seat_order.get(value, 0),
                value,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class AttackingTokenDestinationChoiceHandler:
    operation: str = ATTACKING_TOKEN_DESTINATION_OPERATION
    handler_id: str = "choice.token.attacking-destinations.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 508.4",
        "CR 603.3",
        "CR 702.181",
    )
    capability_dependencies: tuple[str, ...] = (
        "trigger.keyword.mobilize.fixed",
    )
    continuation_fields: tuple[str, ...] = (
        "player",
        "quantity",
        "name",
        "characteristics",
        "_choice_actor",
        "_token_ids",
        "_destinations",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "destinations",
        "legal_actions.choice_schema.legal_refs",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "CreateTokenIntent through replacement-aware token creation",
    )
    replay_fixture: str = "keyword-mobilize-attacking-destinations"
    test_modules: tuple[str, ...] = (
        "tests.test_fixed_keyword_event_effects",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        expected = {"op", "player", "quantity", "name", "characteristics"}
        quantity = effect.get("quantity")
        if (
            set(effect) != expected
            or effect.get("player") != context.actor
            or type(quantity) is not int
            or not 1 <= quantity <= 20
            or effect.get("name") != "Warrior"
            or dict(effect.get("characteristics") or {})
            != {
                "type_line": "Creature — Warrior",
                "power": "1",
                "toughness": "1",
                "colors": ["R"],
            }
        ):
            raise SemanticChoiceError("Mobilize token choice is malformed")
        destinations = _legal_destinations(context.query, context.actor)
        if not destinations:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="no defending recipient"),
            )
        token_ids = tuple(f"mobilize:{index}" for index in range(quantity))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose what each Mobilize token is attacking.",
                choice=DecisionMapChoice(
                    field_name="attacking",
                    legal_refs=token_ids,
                    required=quantity,
                    legal_values=destinations,
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "destinations": destinations,
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_token_ids": token_ids,
                    "_destinations": destinations,
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
        raw = response.get("attacking")
        if not isinstance(raw, Mapping):
            raise SemanticChoiceError("Mobilize assignments must be an object map")
        decisions = {str(key): str(value) for key, value in raw.items()}
        token_ids = tuple(str(value) for value in effect.get("_token_ids", ()))
        destinations = set(
            str(value) for value in effect.get("_destinations", ())
        )
        actor = str(effect.get("_choice_actor") or "")
        if (
            set(decisions) != set(token_ids)
            or any(value not in destinations for value in decisions.values())
            or not destinations.issubset(set(_legal_destinations(query, actor)))
        ):
            raise SemanticChoiceError(
                "Mobilize assignments are stale or incomplete"
            )
        return SemanticChoiceCompletion(
            intents=(
                CreateTokenIntent(
                    actor=actor,
                    controller=actor,
                    name="Warrior",
                    quantity=len(token_ids),
                    characteristics=FrozenMap(
                        dict(effect.get("characteristics") or {})
                    ),
                    tapped=True,
                    attacking_assignments=tuple(
                        decisions[token_id] for token_id in token_ids
                    ),
                    sacrifice_at_end_step=True,
                    reason=str(effect.get("_stack_label") or "Mobilize"),
                ),
            )
        )


ATTACKING_TOKEN_CHOICE_HANDLERS = (
    AttackingTokenDestinationChoiceHandler(),
)


__all__ = [
    "ATTACKING_TOKEN_CHOICE_HANDLERS",
    "ATTACKING_TOKEN_DESTINATION_OPERATION",
    "AttackingTokenDestinationChoiceHandler",
]
