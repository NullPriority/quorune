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
MYRIAD_TOKEN_DESTINATION_OPERATION = "choose_myriad_token_destinations"


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
        "_preview_specs",
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
        replacement_selections = effect.get("_replacement_selections", ())
        if (
            set(effect) - {"_replacement_selections"} != expected
            or not isinstance(replacement_selections, (list, tuple))
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
        preview = tuple(context.query.token_creation_preview())
        groups = tuple(
            str(row.get("_attacking_group") or "")
            for row in preview
            for _ in range(int(row.get("quantity", 1)))
            if row.get("_attacking_group") is not None
        )
        if not preview or any(group != "any" for group in groups):
            raise SemanticChoiceError(
                "Mobilize token replacement preview is malformed"
            )
        token_ids = tuple(f"mobilize:{index}" for index in range(len(groups)))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose what each Mobilize token is attacking.",
                choice=DecisionMapChoice(
                    field_name="attacking",
                    legal_refs=token_ids,
                    required=len(token_ids),
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
                    "_preview_specs": preview,
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
        current_preview = tuple(query.token_creation_preview())
        if (
            set(decisions) != set(token_ids)
            or any(value not in destinations for value in decisions.values())
            or not destinations.issubset(set(_legal_destinations(query, actor)))
            or current_preview != tuple(effect.get("_preview_specs", ()))
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
                    quantity=int(effect.get("quantity", 0)),
                    characteristics=FrozenMap(
                        dict(effect.get("characteristics") or {})
                    ),
                    tapped=True,
                    attacking_assignments=tuple(
                        decisions[token_id] for token_id in token_ids
                    ),
                    attacking_groups=tuple(
                        "any" for _ in range(int(effect.get("quantity", 0)))
                    ),
                    sacrifice_at_end_step=True,
                    reason=str(effect.get("_stack_label") or "Mobilize"),
                    replacement_selections=tuple(
                        effect.get("_replacement_selections", ())
                    ),
                ),
            )
        )


ATTACKING_TOKEN_CHOICE_HANDLERS = (
    AttackingTokenDestinationChoiceHandler(),
)


def _myriad_destinations(
    query: SemanticChoiceQuery,
    opponent: str,
) -> tuple[str, ...]:
    destinations = {opponent} if opponent in query.active_seats else set()
    for row in query.objects(zones=("battlefield",)):
        if "planeswalker" in row.types and row.controller == opponent:
            destinations.add(row.ref)
    return tuple(
        sorted(
            destinations,
            key=lambda value: (0 if value == opponent else 1, value),
        )
    )


@dataclass(frozen=True, slots=True)
class MyriadTokenDestinationChoiceHandler:
    operation: str = MYRIAD_TOKEN_DESTINATION_OPERATION
    handler_id: str = "choice.token.myriad-destinations.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 508.4", "CR 702.116a")
    capability_dependencies: tuple[str, ...] = (
        "token.creation.fixed_copy",
    )
    continuation_fields: tuple[str, ...] = (
        "player",
        "copy_of",
        "copy_snapshot",
        "defending_player",
        "stage",
        "opponents",
        "_choice_actor",
        "_opponents",
        "_destinations",
        "_token_ids",
        "_opponent_by_token",
        "_preview_specs",
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
    replay_fixture: str = "myriad-optional-attacking-copy-destinations"
    test_modules: tuple[str, ...] = (
        "tests.test_fixed_combat_entry_keyword_lifecycles",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        stage = effect.get("stage")
        if stage == "destinations":
            return self._prepare_destinations(effect, context)
        expected = {
            "op",
            "player",
            "copy_of",
            "copy_snapshot",
            "defending_player",
        }
        if (
            set(effect) != expected
            or effect.get("player") != context.actor
            or type(effect.get("copy_of")) is not str
            or not isinstance(effect.get("copy_snapshot"), Mapping)
            or effect.get("defending_player") not in context.query.active_seats
        ):
            raise SemanticChoiceError("Myriad token choice is malformed")
        defending = str(effect["defending_player"])
        opponents = tuple(
            seat
            for seat in context.query.active_seats
            if seat not in {context.actor, defending}
        )
        continuation = FrozenMap(
            {
                **dict(effect),
                "stage": "optional",
                "_choice_actor": context.actor,
                "_opponents": opponents,
                "_stack_label": context.stack_label,
            }
        )
        if not opponents:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=continuation,
                auto_continue=AutoContinue(reason="no other opponent"),
            )
        token_ids = tuple(f"myriad:{opponent}" for opponent in opponents)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    "For each other opponent, choose whether to create a "
                    "Myriad copy."
                ),
                choice=DecisionMapChoice(
                    field_name="attacking",
                    legal_refs=token_ids,
                    required=len(token_ids),
                    legal_values=("create", "decline"),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "opponents": opponents,
                    }
                ),
            ),
            continuation_effect=continuation,
        )

    def _prepare_destinations(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        allowed = {
            "op",
            "player",
            "copy_of",
            "copy_snapshot",
            "defending_player",
            "stage",
            "opponents",
            "_stack_label",
            "_replacement_selections",
        }
        opponents_value = effect.get("opponents")
        if (
            set(effect) - {"_replacement_selections"} != allowed
            - {"_replacement_selections"}
            or effect.get("player") != context.actor
            or not isinstance(opponents_value, (list, tuple))
            or not isinstance(
                effect.get("_replacement_selections", ()), (list, tuple)
            )
        ):
            raise SemanticChoiceError("Myriad destination stage is malformed")
        opponents = tuple(str(value) for value in opponents_value)
        defending = str(effect.get("defending_player") or "")
        legal_opponents = {
            seat
            for seat in context.query.active_seats
            if seat not in {context.actor, defending}
        }
        if (
            len(opponents) != len(set(opponents))
            or any(opponent not in legal_opponents for opponent in opponents)
        ):
            raise SemanticChoiceError("Myriad opponents are stale or malformed")
        preview = tuple(context.query.token_creation_preview())
        opponent_by_token = tuple(
            str(row.get("_attacking_group") or "")
            for row in preview
            for _ in range(int(row.get("quantity", 1)))
            if row.get("_attacking_group") is not None
        )
        if (
            not preview
            or not opponent_by_token
            or any(opponent not in opponents for opponent in opponent_by_token)
        ):
            raise SemanticChoiceError("Myriad token replacement preview is malformed")
        destinations = {
            opponent: _myriad_destinations(context.query, opponent)
            for opponent in opponents
        }
        ordinals: dict[str, int] = {}
        token_ids: list[str] = []
        for opponent in opponent_by_token:
            ordinal = ordinals.get(opponent, 0)
            ordinals[opponent] = ordinal + 1
            token_ids.append(f"myriad:{opponent}:{ordinal}")
        continuation = FrozenMap(
            {
                **dict(effect),
                "_choice_actor": context.actor,
                "_destinations": destinations,
                "_token_ids": tuple(token_ids),
                "_opponent_by_token": opponent_by_token,
                "_preview_specs": preview,
            }
        )
        legal_values = tuple(
            dict.fromkeys(
                destination
                for opponent in opponents
                for destination in destinations[opponent]
            )
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose what each replacement-expanded Myriad copy attacks.",
                choice=DecisionMapChoice(
                    field_name="attacking",
                    legal_refs=tuple(token_ids),
                    required=len(token_ids),
                    legal_values=legal_values,
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "destinations": destinations,
                    }
                ),
            ),
            continuation_effect=continuation,
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        if effect.get("stage") == "optional":
            opponents = tuple(
                str(value) for value in effect.get("_opponents", ())
            )
            raw = response.get("attacking", {})
            decisions = (
                {str(key): str(value) for key, value in raw.items()}
                if isinstance(raw, Mapping)
                else {}
            )
            token_ids = {f"myriad:{opponent}" for opponent in opponents}
            if (
                set(decisions) != token_ids
                or any(value not in {"create", "decline"} for value in decisions.values())
            ):
                raise SemanticChoiceError(
                    "Myriad optional decisions are incomplete"
                )
            selected = tuple(
                opponent
                for opponent in opponents
                if decisions[f"myriad:{opponent}"] == "create"
            )
            if not selected:
                return SemanticChoiceCompletion()
            return SemanticChoiceCompletion(
                prepend_effects=(
                    FrozenMap(
                        {
                            "op": self.operation,
                            "player": str(effect.get("player") or ""),
                            "copy_of": str(effect.get("copy_of") or ""),
                            "copy_snapshot": dict(effect["copy_snapshot"]),
                            "defending_player": str(
                                effect.get("defending_player") or ""
                            ),
                            "stage": "destinations",
                            "opponents": selected,
                            "_stack_label": str(
                                effect.get("_stack_label") or "Myriad"
                            ),
                        }
                    ),
                )
            )
        if effect.get("stage") == "destinations":
            return self._complete_destinations(
                continuation, response, query
            )
        return self._complete_legacy(continuation, response, query)

    def _complete_destinations(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        opponents = tuple(str(value) for value in effect.get("opponents", ()))
        token_ids = tuple(str(value) for value in effect.get("_token_ids", ()))
        opponent_by_token = tuple(
            str(value) for value in effect.get("_opponent_by_token", ())
        )
        raw = response.get("attacking", {})
        decisions = (
            {str(key): str(value) for key, value in raw.items()}
            if isinstance(raw, Mapping)
            else {}
        )
        current_destinations = {
            opponent: _myriad_destinations(query, opponent)
            for opponent in opponents
        }
        expected_destinations = effect.get("_destinations")
        current_preview = tuple(query.token_creation_preview())
        if (
            len(token_ids) != len(opponent_by_token)
            or set(decisions) != set(token_ids)
            or not isinstance(expected_destinations, Mapping)
            or current_preview != tuple(effect.get("_preview_specs", ()))
            or any(
                tuple(expected_destinations.get(opponent, ()))
                != current_destinations[opponent]
                for opponent in opponents
            )
            or any(
                decisions[token_id] not in current_destinations[opponent]
                for token_id, opponent in zip(token_ids, opponent_by_token)
            )
        ):
            raise SemanticChoiceError(
                "Myriad assignments are stale, illegal, or incomplete"
            )
        actor = str(effect.get("player") or "")
        return SemanticChoiceCompletion(
            intents=(
                CreateTokenIntent(
                    actor=actor,
                    controller=actor,
                    name="",
                    quantity=len(opponents),
                    copy_of=str(effect.get("copy_of") or ""),
                    copy_snapshot=FrozenMap(effect["copy_snapshot"]),
                    tapped=True,
                    attacking_assignments=tuple(
                        decisions[token_id] for token_id in token_ids
                    ),
                    attacking_groups=opponents,
                    exile_at_end_of_combat=True,
                    reason=str(effect.get("_stack_label") or "Myriad"),
                    replacement_selections=tuple(
                        effect.get("_replacement_selections", ())
                    ),
                ),
            )
        )

    def _complete_legacy(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        opponents = tuple(str(value) for value in effect.get("_opponents", ()))
        raw_destinations = effect.get("_destinations")
        if not isinstance(raw_destinations, Mapping):
            raise SemanticChoiceError("Myriad destination context is malformed")
        current = {
            opponent: _myriad_destinations(query, opponent)
            for opponent in opponents
        }
        expected = {
            opponent: tuple(
                str(value)
                for value in raw_destinations.get(opponent, ())
            )
            for opponent in opponents
        }
        raw = response.get("attacking", {})
        decisions = (
            {str(key): str(value) for key, value in raw.items()}
            if isinstance(raw, Mapping)
            else {}
        )
        token_ids = {f"myriad:{opponent}" for opponent in opponents}
        if (
            current != expected
            or set(decisions) != token_ids
            or any(
                decisions[f"myriad:{opponent}"]
                not in {"decline", *current[opponent]}
                for opponent in opponents
            )
        ):
            raise SemanticChoiceError(
                "Myriad assignments are stale, illegal, or incomplete"
            )
        assignments = tuple(
            decisions[f"myriad:{opponent}"]
            for opponent in opponents
            if decisions[f"myriad:{opponent}"] != "decline"
        )
        return SemanticChoiceCompletion(
            intents=(
                CreateTokenIntent(
                    actor=str(effect.get("_choice_actor") or ""),
                    controller=str(effect.get("_choice_actor") or ""),
                    name="",
                    quantity=len(assignments),
                    copy_of=str(effect.get("copy_of") or ""),
                    copy_snapshot=(
                        FrozenMap(effect["copy_snapshot"])
                        if isinstance(effect.get("copy_snapshot"), Mapping)
                        else None
                    ),
                    tapped=True,
                    attacking_assignments=assignments,
                    exile_at_end_of_combat=True,
                    reason=str(effect.get("_stack_label") or "Myriad"),
                ),
            )
            if assignments
            else ()
        )


ATTACKING_TOKEN_CHOICE_HANDLERS = (
    AttackingTokenDestinationChoiceHandler(),
    MyriadTokenDestinationChoiceHandler(),
)


__all__ = [
    "ATTACKING_TOKEN_CHOICE_HANDLERS",
    "ATTACKING_TOKEN_DESTINATION_OPERATION",
    "MYRIAD_TOKEN_DESTINATION_OPERATION",
    "AttackingTokenDestinationChoiceHandler",
    "MyriadTokenDestinationChoiceHandler",
]
