from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _player(value: Any, *, field: str, active_seats: tuple[str, ...]) -> str:
    if type(value) is not str or value not in active_seats:
        raise SemanticChoiceError(f"{field} must be an active player")
    return value


def _count(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise SemanticChoiceError(
            "An optional draw requires a positive integer count"
        )
    return value


def _private(value: Any) -> bool:
    if type(value) is not bool:
        raise SemanticChoiceError(
            "An optional draw private flag must be a boolean"
        )
    return value


@dataclass(frozen=True, slots=True)
class OptionalDrawHandler:
    """CR 121.2b/121.3 choice legality for the prospective drawer."""

    operation: str = "offer_draw"
    handler_id: str = "choice.draw.optional.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 121.2b",
        "CR 121.3",
        "CR 121.3a",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )
    continuation_fields: tuple[str, ...] = (
        "player",
        "drawer",
        "count",
        "private",
        "event_optional_draw_version",
    )
    private_data: tuple[str, ...] = ("drawn card",)
    projected_fields: tuple[str, ...] = (
        "prompt",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "drawing.DrawPermission",
        "drawing.begin_draw_sequence",
    )
    replay_fixture: str = "optional-draw-choice"
    test_modules: tuple[str, ...] = (
        "tests.test_optional_draw_choices",
    )

    def _resolved(
        self,
        effect: Mapping[str, Any],
        *,
        actor: str,
        active_seats: tuple[str, ...],
    ) -> tuple[str, str, int, bool]:
        allowed = {"op", "player", "drawer", "count", "private"}
        if 'event_optional_draw_version' in effect:
            from ..rules.event_optional_draw_shapes import validate_event_optional_draw_payload
            try:validate_event_optional_draw_payload(effect, materialized=True)
            except (TypeError, ValueError) as exc:raise SemanticChoiceError(str(exc)) from exc
            allowed.add('event_optional_draw_version')
        if set(effect) - allowed:
            raise SemanticChoiceError("Optional draw fields are invalid")
        if effect.get("op") != self.operation:
            raise SemanticChoiceError("Optional draw operation is invalid")
        chooser = _player(
            effect.get("player", actor),
            field="Optional-draw chooser",
            active_seats=active_seats,
        )
        if chooser != actor:
            raise SemanticChoiceError(
                "Optional-draw choice must be issued to its chooser"
            )
        drawer = _player(
            effect.get("drawer", chooser),
            field="Prospective drawer",
            active_seats=active_seats,
        )
        count = _count(effect.get("count", 1))
        private = _private(effect.get("private", True))
        return chooser, drawer, count, private

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        if effect.get('event_optional_draw_version') == 2:
            from ..rules.event_creature_controller_results import death_controller_draw_is_unavailable
            try:
                unavailable = death_controller_draw_is_unavailable(effect, actor=context.actor,
                    seats=context.query.seats, active_seats=context.query.active_seats)
            except (KeyError, TypeError, ValueError) as exc:raise SemanticChoiceError(str(exc)) from exc
            if unavailable:
                return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                    auto_continue=AutoContinue('The original prospective drawer has left the game'))
        chooser, drawer, count, private = self._resolved(
            effect,
            actor=context.actor,
            active_seats=context.query.active_seats,
        )
        permission = context.query.draw_permission(drawer)
        values = {
                "op": self.operation,
                "player": chooser,
                "drawer": drawer,
                "count": count,
                "private": private,
            }
        if 'event_optional_draw_version' in effect:values['event_optional_draw_version'] = effect['event_optional_draw_version']
        continuation = FrozenMap(values)
        if not permission.allows_complete_draw(count):
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=continuation,
                auto_continue=AutoContinue(
                    "the prospective drawer cannot legally draw that many cards"
                ),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    f"Draw {count} card{'s' if count != 1 else ''}?"
                    if chooser == drawer
                    else (
                        f"Have {drawer} draw {count} "
                        f"card{'s' if count != 1 else ''}?"
                    )
                ),
                choice=ScalarChoice(
                    field_name="choice",
                    legal_values=("draw", "decline"),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "drawer": drawer,
                        "count": count,
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
        if 'event_optional_draw_version' in continuation.effect:
            allowed = {'choice', 'action', 'action_id', 'a', 'cap', 'reason', 'plan', 'next', 'choice_schema', 'label'}
            if set(response) - allowed:
                raise SemanticChoiceError('The event draw response cannot change its participant or instruction')
        actor = continuation.effect.get("player")
        if type(actor) is not str:
            raise SemanticChoiceError(
                "Optional-draw continuation chooser is malformed"
            )
        chooser, drawer, count, private = self._resolved(
            continuation.effect,
            actor=actor,
            active_seats=query.active_seats,
        )
        choice = response.get("choice")
        if type(choice) is not str or choice not in {"draw", "decline"}:
            raise SemanticChoiceError("Choose draw or decline")
        if choice == "decline":
            return SemanticChoiceCompletion()
        if not query.draw_permission(drawer).allows_complete_draw(count):
            raise SemanticChoiceError(
                "The prospective drawer can no longer legally draw that many cards"
            )
        return SemanticChoiceCompletion(
            prepend_effects=(
                FrozenMap(
                    {
                        "op": "draw",
                        "player": drawer,
                        "count": count,
                        "private": private,
                    }
                ),
            )
        )


OPTIONAL_DRAW_CHOICE_HANDLERS = (OptionalDrawHandler(),)


__all__ = ["OPTIONAL_DRAW_CHOICE_HANDLERS", "OptionalDrawHandler"]
