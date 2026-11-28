from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..delayed_triggers import NEXT_TURN_COUNTER_DRAW_FIELD
from ..replacement.immutable import FrozenMap
from ..rules.event_optional_draw_shapes import (
    CAST_OPPONENT_DRAW_VERSION,
    DEFENDING_CONTROLLER_DRAW_VERSION,
    EVENT_OPTIONAL_DRAW_FIELD,
    TABLE_DRAW_ATTACK_RESTRICTION_VERSION,
    validate_event_optional_draw_payload,
)
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


def _cast_opponents(query: SemanticChoiceQuery, participant: str) -> tuple[str, ...]:
    if participant not in query.seats:
        raise SemanticChoiceError("Optional cast opponent draws require a known original caster")
    return tuple(seat for seat in query.apnap_order if seat != participant)


def _group_draw_stage_fields(effect):
    if effect[EVENT_OPTIONAL_DRAW_FIELD] == CAST_OPPONENT_DRAW_VERSION:
        return "cast_draw_order", "cast_draw_choices"
    return "table_draw_order", "table_draw_choices"


def _group_draw_stage(effect, query):
    try:
        validate_event_optional_draw_payload(effect, materialized=True)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if effect[EVENT_OPTIONAL_DRAW_FIELD] == CAST_OPPONENT_DRAW_VERSION:
        order = _cast_opponents(query, effect["cast_participant"])
    else:
        if effect["protected_player"] not in query.active_seats:
            raise SemanticChoiceError("Optional table draws require their current protected controller")
        order = query.apnap_order
    if "player" not in effect:
        return order, ()
    order_field, choices_field = _group_draw_stage_fields(effect)
    recorded_order = effect[order_field]
    choices = effect[choices_field]
    if (not isinstance(recorded_order, (tuple, list)) or tuple(recorded_order) != order
            or not isinstance(choices, (tuple, list)) or len(choices) >= len(order)
            or any(type(choice) is not str or choice not in ("draw", "decline", "unavailable") for choice in choices)
            or effect["player"] != order[len(choices)]):
        raise SemanticChoiceError("Optional draw continuation changed its ordered choices")
    return order, tuple(choices)


def _advance_group_draw(effect, order, choices):
    if len(choices) < len(order):
        order_field, choices_field = _group_draw_stage_fields(effect)
        return (FrozenMap({**effect, "player": order[len(choices)], order_field: order, choices_field: choices}),)
    players = tuple(seat for seat, answer in zip(order, choices) if answer == "draw")
    if not players:
        return ()
    draw = FrozenMap({"op": "draw_each_player", "players": players, "count": 1, "private": True})
    if effect[EVENT_OPTIONAL_DRAW_FIELD] == CAST_OPPONENT_DRAW_VERSION:
        return (draw,)
    protected_player = effect["protected_player"]
    opponents = tuple(seat for seat in players if seat != protected_player)
    if not opponents:
        return (draw,)
    from ..next_turn_attack_restriction_model import ATTACK_RESTRICTION_OPERATION

    restriction = FrozenMap({
        "op": ATTACK_RESTRICTION_OPERATION, "schema_version": 1,
        "protected_player": protected_player, "opponents": opponents,
    })
    return draw, restriction


def _prepare_group_draw(effect, context):
    order, choices = _group_draw_stage(effect, context.query)
    protects = effect.get("protected_player")
    if protects is not None and protects != context.stack_controller:
        raise SemanticChoiceError("Optional table draws require the original trigger controller")
    continuation = FrozenMap(effect)
    if "player" not in effect:
        return SemanticChoicePreparation(request=None, continuation_effect=continuation,
            auto_continue=AutoContinue(
                "Order the players' choices" if protects is not None else "Order the opponents' choices",
                _advance_group_draw(effect, order, choices),
            ))
    if context.actor != effect["player"]:
        raise SemanticChoiceError("Optional group draw requires its next chooser")
    if not context.query.draw_permission(context.actor).allows_complete_draw(1):
        return SemanticChoicePreparation(request=None, continuation_effect=continuation,
            auto_continue=AutoContinue("The prospective drawer cannot legally draw",
                _advance_group_draw(effect, order, (*choices, "unavailable"))))
    previous = tuple({"player": seat, "choice": answer}
                     for seat, answer in zip(order, choices) if answer != "unavailable")
    prompt = "Draw 1 card?"
    if protects is not None and context.actor != protects:
        prompt += f" If you choose to draw, you can't attack {protects} or permanents they control during your next turn."
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=prompt,
            choice=ScalarChoice(field_name="choice", legal_values=("draw", "decline")),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "offer_draw",
                "drawer": context.actor, "count": 1, "previous_choices": previous}),
        ),
        continuation_effect=continuation,
    )


def _complete_group_draw(effect, response, query, *, controller):
    order, choices = _group_draw_stage(effect, query)
    if effect.get("protected_player", controller) != controller:
        raise SemanticChoiceError("Optional table draws require the original trigger controller")
    actor = effect.get("player")
    choice = response.get("choice")
    if actor is None or type(choice) is not str or choice not in ("draw", "decline"):
        raise SemanticChoiceError("Choose draw or decline for the current player")
    if choice == "draw" and not query.draw_permission(actor).allows_complete_draw(1):
        raise SemanticChoiceError("The prospective drawer can no longer legally draw")
    return SemanticChoiceCompletion(prepend_effects=_advance_group_draw(effect, order, (*choices, choice)))


def _next_turn_counter_draw_recipient(effect, query):
    raw_fields = {"op", NEXT_TURN_COUNTER_DRAW_FIELD, "recipient", "private"}
    if (set(effect) not in (raw_fields, raw_fields | {"player"}) or effect["op"] != "offer_draw"
            or type(effect[NEXT_TURN_COUNTER_DRAW_FIELD]) is not int or effect[NEXT_TURN_COUNTER_DRAW_FIELD] != 1
            or effect["private"] is not True or type(effect["recipient"]) is not str
            or effect["recipient"] not in query.seats or effect.get("player", effect["recipient"]) != effect["recipient"]):
        raise SemanticChoiceError("Delayed draw requires its original known spell controller")
    return effect["recipient"]


def _next_turn_counter_draw_counts(query, recipient):
    permission = query.draw_permission(recipient)
    return (0, *(count for count in (1, 2) if permission.allows_complete_draw(count)))


def _prepare_next_turn_counter_draw(effect, context):
    recipient = _next_turn_counter_draw_recipient(effect, context.query)
    continuation = FrozenMap(effect)
    if recipient not in context.query.active_seats:
        return SemanticChoicePreparation(request=None, continuation_effect=continuation,
            auto_continue=AutoContinue("The original spell controller has left the game"))
    if "player" not in effect:
        if context.actor != context.stack_controller:
            raise SemanticChoiceError("The resolving delayed ability must offer its original recipient the draw")
        return SemanticChoicePreparation(request=None, continuation_effect=continuation,
            auto_continue=AutoContinue("Ask the original spell controller how many cards to draw",
                (FrozenMap({**effect, "player": recipient}),)))
    if context.actor != recipient:
        raise SemanticChoiceError("Only the original spell controller may choose the draw amount")
    counts = _next_turn_counter_draw_counts(context.query, recipient)
    if counts == (0,):
        return SemanticChoicePreparation(request=None, continuation_effect=continuation,
            auto_continue=AutoContinue("The original spell controller cannot choose a positive draw"))
    return SemanticChoicePreparation(request=SemanticChoiceRequest(
        prompt="Choose how many cards to draw.",
        choice=ScalarChoice("count", counts),
        public_context=FrozenMap({"stack": context.stack_ref, "drawer": recipient}),
    ), continuation_effect=continuation)


def _complete_next_turn_counter_draw(effect, response, query):
    recipient = _next_turn_counter_draw_recipient(effect, query)
    allowed = {"count", "action", "action_id", "a", "cap", "reason", "plan", "next", "choice_schema", "label"}
    count = response.get("count")
    if (set(response) - allowed or effect.get("player") != recipient or recipient not in query.active_seats
            or type(count) is not int or count not in _next_turn_counter_draw_counts(query, recipient)):
        raise SemanticChoiceError("Choose one legal original-controller draw count")
    if not count:
        return SemanticChoiceCompletion()
    return SemanticChoiceCompletion(prepend_effects=(
        FrozenMap({"op": "draw", "player": recipient, "count": count, "private": True}),
    ))


def _defending_controller_draw_after_effects():
    from ..combat_removal import COMBAT_REMOVAL_OPERATION

    return (FrozenMap({"op": COMBAT_REMOVAL_OPERATION, "card": "$source.zone_object", "untap": "before"}),)


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
        "cast_participant",
        "cast_draw_order",
        "cast_draw_choices",
        "protected_player",
        "table_draw_order",
        "table_draw_choices",
        NEXT_TURN_COUNTER_DRAW_FIELD,
        "recipient",
    )
    private_data: tuple[str, ...] = ("drawn card",)
    projected_fields: tuple[str, ...] = (
        "prompt",
        "legal_actions.choice_schema.legal_values",
        "previous_choices",
    )
    mutation_path: tuple[str, ...] = (
        "drawing.DrawPermission",
        "drawing.begin_draw_sequence",
    )
    replay_fixture: str = "optional-draw-choice"
    test_modules: tuple[str, ...] = (
        "tests.test_optional_draw_choices",
    )

    def requires_object_rows(self, effect: Mapping[str, Any]) -> bool:
        return bool(set(effect) - {"op", "player", "drawer", "count", "private"})

    def _resolved(
        self,
        effect: Mapping[str, Any],
        *,
        actor: str,
        active_seats: tuple[str, ...],
    ) -> tuple[str, str, int, bool]:
        allowed = {"op", "player", "drawer", "count", "private"}
        if 'event_optional_draw_version' in effect:
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
        from ..rules.event_optional_draw_shapes import DAMAGE_DEALER_DRAW_VERSION

        from .source_counter_mill import prepare_counter_mill_offer
        from ..rules.source_counter_mill_choice import OFFER_FIELD

        if OFFER_FIELD in effect:
            return prepare_counter_mill_offer(effect, context)
        if NEXT_TURN_COUNTER_DRAW_FIELD in effect:
            return _prepare_next_turn_counter_draw(effect, context)

        if effect.get(EVENT_OPTIONAL_DRAW_FIELD) in (CAST_OPPONENT_DRAW_VERSION, TABLE_DRAW_ATTACK_RESTRICTION_VERSION):
            return _prepare_group_draw(effect, context)
        if effect.get(EVENT_OPTIONAL_DRAW_FIELD) == DEFENDING_CONTROLLER_DRAW_VERSION:
            try:
                validate_event_optional_draw_payload(effect, materialized=True)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            if (effect["player"] != context.actor or context.actor not in context.query.seats
                    or effect["drawer"] != context.stack_controller):
                raise SemanticChoiceError("The defender chooses for the original attack trigger controller")
            if context.actor not in context.query.active_seats:
                return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                    auto_continue=AutoContinue("The original defending player has left the game"))
        if effect.get("event_optional_draw_version") == DAMAGE_DEALER_DRAW_VERSION:
            try:
                validate_event_optional_draw_payload(effect, materialized=True)
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            if effect["player"] != context.actor or context.actor not in context.query.seats:
                raise SemanticChoiceError("Damage dealer draw requires a known prospective controller")
            if context.actor not in context.query.active_seats:
                return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                    auto_continue=AutoContinue("The creature's prospective drawer has left the game"))
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
        from .source_counter_mill import complete_counter_mill_offer
        from ..rules.source_counter_mill_choice import OFFER_FIELD

        if OFFER_FIELD in continuation.effect:
            return complete_counter_mill_offer(continuation, response, query)
        if NEXT_TURN_COUNTER_DRAW_FIELD in continuation.effect:
            return _complete_next_turn_counter_draw(continuation.effect, response, query)
        if 'event_optional_draw_version' in continuation.effect:
            allowed = {'choice', 'action', 'action_id', 'a', 'cap', 'reason', 'plan', 'next', 'choice_schema', 'label'}
            if set(response) - allowed:
                raise SemanticChoiceError('The event draw response cannot change its participant or instruction')
        if continuation.effect.get(EVENT_OPTIONAL_DRAW_FIELD) in (
            CAST_OPPONENT_DRAW_VERSION, TABLE_DRAW_ATTACK_RESTRICTION_VERSION,
        ):
            return _complete_group_draw(
                continuation.effect, response, query, controller=continuation.semantic_frame.controller,
            )
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
        defending_controller_draw = continuation.effect.get(EVENT_OPTIONAL_DRAW_FIELD) == DEFENDING_CONTROLLER_DRAW_VERSION
        if defending_controller_draw and drawer != continuation.semantic_frame.controller:
            raise SemanticChoiceError("The defender's draw requires the original attack trigger controller")
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
                *(_defending_controller_draw_after_effects() if defending_controller_draw else ()),
            )
        )


OPTIONAL_DRAW_CHOICE_HANDLERS = (OptionalDrawHandler(),)


__all__ = ["OPTIONAL_DRAW_CHOICE_HANDLERS", "OptionalDrawHandler"]
