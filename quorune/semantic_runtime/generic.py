from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..drawing.model import (
    DiscardDrawnCardUnlessType,
    RevealDrawnCard,
    drawn_card_action_from_dict,
)
from ..rules.faction_trigger_model import CountFactionDraw, FACTION_DRAW_OBSERVER_FIELD
from ..rules.opponent_draw_result import ObserveOpponentDraw, OPPONENT_DRAW_OBSERVER_FIELD
from ..fractional_quantity_model import HalfQuantitySpec
from ..query_effect_amount_model import OPPONENT_HAND_MILL_KIND, OpponentHandMillCount
from ..selected_graveyard_mill_count import SELECTED_GRAVEYARD_MILL_KIND, SelectedGraveyardMillCount
from ..target_cardinality import ordered_selected_players
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import (
    BecomeMonarchIntent,
    DrawCardsIntent,
    IntentPlan,
    MillCardsIntent,
)
from .nodes import (
    BecomeMonarchNode,
    DrawEachPlayerNode,
    DrawNode,
)


def _count(effect: Mapping[str, Any]) -> int:
    value = effect.get("count", 1)
    if type(value) is not int or value < 0:
        raise SemanticNodeError("Draw count must be a nonnegative integer")
    return value


def _reason(
    effect: Mapping[str, Any], context: ReadOnlyHandlerContext
) -> str:
    return str(effect.get("reason") or context.default_reason)


def _private(effect: Mapping[str, Any]) -> bool:
    value = effect.get("private", False)
    if type(value) is not bool:
        raise SemanticNodeError("Draw private flag must be a boolean")
    return value


@dataclass(frozen=True, slots=True)
class DrawHandler:
    handler_id: str = "generic.draw.v1"
    schema_version: int = 1
    family: str = "zone.draw"
    operation: str = "draw"
    rule_references: tuple[str, ...] = ("121.1", "121.2")
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        from ..rules.die_roll_draw import ROLL_FIELD

        if ROLL_FIELD in effect:
            raise SemanticNodeError("A single d20 draw must be bound by its resolving instruction")
        node = DrawNode(
            player=context.query.require_known_seat(
                str(effect.get("player") or context.actor)
            ),
            count=_count(effect),
            reason=_reason(effect, context),
            private=_private(effect),
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                DrawCardsIntent(
                    player=node.player,
                    count=node.count,
                    reason=node.reason,
                    private=node.private,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class MillHandler:
    handler_id: str = "generic.mill.v1"
    schema_version: int = 2
    family: str = "zone.mill"
    operation: str = "mill"
    rule_references: tuple[str, ...] = (
        "400.7",
        "701.17",
        "701.17a",
        "701.17b",
        "701.17c",
        "608.2f",
        "101.4",
    )
    capability_dependencies: tuple[str, ...] = ("zone.mill.fixed",)

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        from ..rules.source_counter_mill_choice import COMMIT_FIELD

        if COMMIT_FIELD in effect:
            return _source_counter_mill_plan(effect, context, handler_id=self.handler_id)
        if set(effect) - {"op", "player", "players", "scope", "count", "reason", "_replacement_selections", "followup"}:
            raise SemanticNodeError("Mill effects have a closed schema")
        selections = effect.get("_replacement_selections", ())
        if not isinstance(selections, (list, tuple)):
            raise SemanticNodeError("Mill replacement selections require a sequence")
        if "scope" in effect and (
            effect["scope"] not in {"all_active_players","controller_opponents"} or "player" in effect
        ):
            raise SemanticNodeError("Mill recipient scope is malformed")
        recipients = tuple(seat for seat in context.query.apnap_order
                           if effect.get("scope") != "controller_opponents" or seat != context.actor)
        if "players" in effect:
            if "scope" in effect or "player" in effect:
                raise SemanticNodeError("Mill has conflicting recipient scopes")
            recipients = _selected_players(effect, context)
        raw_count = effect.get("count", 1)
        followup = _mill_followup(effect, raw_count, context)
        live_count = isinstance(raw_count, Mapping) and raw_count.get("kind") == OPPONENT_HAND_MILL_KIND
        try:
            if live_count:
                count = OpponentHandMillCount.from_dict(raw_count, scope=effect.get("scope"))
            elif isinstance(raw_count, Mapping) and raw_count.get("kind") == SELECTED_GRAVEYARD_MILL_KIND:
                if "players" not in effect:
                    raise ValueError("Graveyard-count Mill needs the original selected players")
                count = SelectedGraveyardMillCount.from_dict(raw_count)
            elif isinstance(raw_count, Mapping):
                count = HalfQuantitySpec.from_dict(raw_count)
            else:
                count = _count(effect)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if ("players" in effect or effect.get("scope") == "controller_opponents") and not recipients:
            return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(), result_shape="by_player")
        player = context.query.require_active_seat(
            str(effect.get("player") or context.actor)
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                MillCardsIntent(
                    actor=context.actor,
                    player=player,
                    count=count,
                    reason=_reason(effect, context),
                    batch_players=recipients if "scope" in effect or "players" in effect else (),
                    replacement_selections=tuple(selections),
                    followup=followup,
                ),
            ),
        )


def _source_counter_mill_plan(effect, context, *, handler_id):
    from ..rules.source_counter_mill_choice import COMMIT_FIELD
    from .domain_intent import DomainEffectIntent

    marker = effect[COMMIT_FIELD]
    if (context.source is None or not isinstance(marker, Mapping)
            or marker.get("stack") != context.source.stack_ref or effect.get("player") != context.actor):
        raise SemanticNodeError("The committed Mill instruction requires its resolving source")
    return IntentPlan(operation="mill", handler_id=handler_id, intents=(DomainEffectIntent(
        context.actor, "mill", effect, _reason(effect, context),
    ),))


def _mill_followup(effect, count, context):
    from ..rules.milled_creature_land_to_hand import MillCreatureLandToHand, uses_milled_creature_land_to_hand

    if "followup" not in effect:
        return None
    from ..rad_counter_model import RAD_EFFECT_FIELD
    from ..rad_counters import RadMillFollowup
    from ..rules.committed_mill_cards import MillAdventuresToHand, uses_committed_mill_result
    from ..rules.mill_paid_hand_return import MillPaidHandReturn, uses_mill_paid_hand_return
    from ..rules.milled_creature_tokens import MilledCreatureTokens
    from ..rules.milled_permanent_battlefield import MillOnePermanentToBattlefield, uses_milled_permanent_battlefield
    from ..rules.milled_permanent_recovery import (
        MilledPermanentRecovery, uses_milled_permanent_recovery, validate_recovery_mill,
    )

    try:
        result = effect["followup"]
        if uses_milled_permanent_recovery(effect):
            validate_recovery_mill(effect, actor=context.actor)
            if context.source is None:
                raise ValueError("Permanent Mill recovery requires its resolving attack source")
            return MilledPermanentRecovery(context.source.stack_ref)
        if isinstance(result, Mapping) and RAD_EFFECT_FIELD in result:
            if (set(effect) - {"_replacement_selections"} != {"op", "player", "count", "reason", "followup"}
                    or effect["player"] != context.actor or context.source is None
                    or context.source.object_id is not None or type(count) is not int or count <= 0):
                raise ValueError("Radiation Mill requires its original sourceless controller")
            return RadMillFollowup.from_dict(result)
        permanent_choice = uses_milled_permanent_battlefield(effect)
        paid_hand = uses_mill_paid_hand_return(effect)
        if uses_committed_mill_result(effect) or permanent_choice or paid_hand:
            result_type = MillOnePermanentToBattlefield if permanent_choice else (
                MillPaidHandReturn if paid_hand else MillAdventuresToHand
            )
            committed = result_type.from_dict(result)
            if (
                set(effect) - {"_replacement_selections", "reason"} != {"op", "player", "count", "followup"}
                or effect["player"] != context.actor or type(count) is not int or count != committed.effect()["count"]
            ):
                raise ValueError("The committed public result requires fixed controller Mill")
            return committed
        if uses_milled_creature_land_to_hand(effect):
            committed = MillCreatureLandToHand.from_dict(result)
            if (set(effect) - {"_replacement_selections", "reason"} != {"op", "player", "count", "followup"}
                    or effect["player"] != context.actor or type(count) is not int or count != committed.effect()["count"]):
                raise ValueError("Committed Mill hand selection requires its original controller Mill")
            return committed
        if effect.get("scope") != "all_active_players" or type(count) is not int or "count" not in effect:
            raise ValueError("The committed creature result requires all-player Mill and its resolved count")
        return MilledCreatureTokens.from_dict(effect["followup"])
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc


def _selected_players(effect, context):
    try:
        return ordered_selected_players(effect["players"], context.query.apnap_order)
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class DrawEachPlayerHandler:
    handler_id: str = "generic.draw-each-player.v1"
    schema_version: int = 2
    family: str = "zone.draw"
    operation: str = "draw_each_player"
    rule_references: tuple[str, ...] = ("121.1", "121.2")
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        observers = ()
        if FACTION_DRAW_OBSERVER_FIELD in effect:
            try:
                observer = CountFactionDraw.from_dict(effect[FACTION_DRAW_OBSERVER_FIELD])
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
            if (observer.controller != context.actor or effect.get("scope") != "controller_opponents"
                    or _count(effect) != 1 or effect.get("private") is not True):
                raise SemanticNodeError("The faction observer requires its original private opponent draw")
            observers = (observer,)
        if OPPONENT_DRAW_OBSERVER_FIELD in effect:
            try:
                opponent_observer = ObserveOpponentDraw.from_dict(effect[OPPONENT_DRAW_OBSERVER_FIELD])
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
            if (observers or opponent_observer.controller != context.actor
                    or effect.get("scope") != "controller_opponents"
                    or _count(effect) != 1 or effect.get("private") is not True):
                raise SemanticNodeError("A distinct opponent receipt requires its original private opponent draw")
            observers = (opponent_observer,)
        if "scope" in effect:
            shared = effect["scope"] == "controller_and_opponents"
            fields = {"op", "scope", "count", "private", "reason"}
            if observers:
                fields.add(FACTION_DRAW_OBSERVER_FIELD if isinstance(observers[0], CountFactionDraw)
                           else OPPONENT_DRAW_OBSERVER_FIELD)
            if shared:
                fields.add("controller_count")
            if ((not shared and effect["scope"] != "controller_opponents")
                    or set(effect) - fields
                    or shared and (type(effect.get("controller_count")) is not int
                                   or effect["controller_count"] < 1)
                    or _count(effect) < 1):
                raise SemanticNodeError("Opponent draw recipients require a closed positive-count scope")
            controller = context.query.require_known_seat(context.actor)
            players = tuple(seat for seat in context.query.apnap_order if shared or seat != controller)
        else:
            if "controller_count" in effect:
                raise SemanticNodeError("A controller draw count requires the shared recipient scope")
            if "players" in effect and set(effect) - {"op", "players", "count", "private", "reason"}:
                raise SemanticNodeError("Selected draw recipients conflict with another scope")
            players = _selected_players(effect, context) if "players" in effect else context.query.apnap_order
        node = DrawEachPlayerNode(
            count=_count(effect),
            reason=_reason(effect, context),
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=tuple(
                DrawCardsIntent(
                    player=seat,
                    count=effect.get("controller_count", node.count) if seat == context.actor else node.count,
                    reason=node.reason,
                    private=True,
                    post_draw_actions=observers,
                )
                for seat in players
            ),
            result_shape="by_player",
        )


@dataclass(frozen=True, slots=True)
class DrawWithActionsHandler:
    """Lower the closed CR 121.6c specifically-drawn-card action family."""

    handler_id: str = "generic.draw-with-actions.v1"
    schema_version: int = 1
    family: str = "zone.draw"
    operation: str = "draw_with_actions"
    rule_references: tuple[str, ...] = ("121.1", "121.6c")
    capability_dependencies: tuple[str, ...] = (
        "zone.draw.library_to_hand",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        allowed = {
            "op",
            "player",
            "count",
            "private",
            "reason",
            "post_draw_actions",
        }
        if set(effect) - allowed:
            raise SemanticNodeError(
                "Draw-with-actions carries unsupported fields"
            )
        if _count(effect) != 1:
            raise SemanticNodeError(
                "Draw-with-actions requires exactly one draw"
            )
        raw_actions = effect.get("post_draw_actions")
        if not isinstance(raw_actions, (list, tuple)) or any(
            not isinstance(value, Mapping) for value in raw_actions
        ):
            raise SemanticNodeError(
                "Draw-with-actions requires typed action objects"
            )
        try:
            actions = tuple(
                drawn_card_action_from_dict(value)
                for value in raw_actions
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        faction = (len(actions) == 1 and isinstance(actions[0], CountFactionDraw)
                   and actions[0].controller == context.actor and effect.get("private") is True)
        opponent = (len(actions) == 1 and isinstance(actions[0], ObserveOpponentDraw)
                    and actions[0].controller == context.actor and effect.get("private") is True
                    and context.source is not None and actions[0].stack_ref == context.source.stack_ref
                    and effect.get("player") != context.actor)
        if not (faction or opponent) and actions != (
            RevealDrawnCard(),
            DiscardDrawnCardUnlessType(card_type="land"),
        ):
            raise SemanticNodeError(
                "Draw-with-actions supports only public reveal then "
                "discard-unless-land"
            )
        player = context.query.require_known_seat(
            str(effect.get("player") or context.actor)
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                DrawCardsIntent(
                    player=player,
                    count=1,
                    reason=_reason(effect, context),
                    private=_private(effect),
                    post_draw_actions=actions,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class BecomeMonarchHandler:
    handler_id: str = "generic.become-monarch.v1"
    schema_version: int = 1
    family: str = "variant.monarch"
    operation: str = "become_monarch"
    rule_references: tuple[str, ...] = ("725.1", "725.2")
    capability_dependencies: tuple[str, ...] = (
        "variant.monarch.designate",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        node = BecomeMonarchNode(
            player=context.query.require_active_seat(
                str(effect.get("player") or context.actor)
            ),
            reason=_reason(effect, context),
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                BecomeMonarchIntent(
                    player=node.player,
                    reason=node.reason,
                ),
            ),
        )


GENERIC_HANDLERS = (
    DrawHandler(),
    MillHandler(),
    DrawEachPlayerHandler(),
    DrawWithActionsHandler(),
    BecomeMonarchHandler(),
)
