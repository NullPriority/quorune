from __future__ import annotations

"""Commit the designation and materialize its source-less triggered abilities."""

from .dungeon_catalog import VentureMode
from .dungeon_progress import DUNGEON_VENTURE_FIELD
from .errors import GameRuleError
from .initiative_model import (
    INHERENT_INITIATIVE_TAKE, INHERENT_INITIATIVE_VENTURE, INITIATIVE_TRIGGER_PLAYER,
    TAKE_INITIATIVE_FIELD, TakeInitiativeIntent, initiative_effect_player,
)
from .model import StackItem
from .trigger_processing import enqueue_trigger_batch


def _initiative_trigger(host, *, controller: str, player: str, key: str, label: str, effect: dict) -> StackItem:
    ref = host._next_ref("S")
    return StackItem(
        stack_id=host._stable_runtime_id("stack", ref), ref=ref, kind="triggered_ability",
        controller=controller, label=label, visibility=list(host.seats), semantic_key=key,
        context={"inherent_rule": "CR 726.2", INITIATIVE_TRIGGER_PLAYER: player, "dynamic_effects": [effect]},
    )


def _venture_trigger(host, player: str) -> StackItem:
    return _initiative_trigger(
        host, controller=player, player=player, key=INHERENT_INITIATIVE_VENTURE,
        label="The initiative — venture into Undercity",
        effect={"op": "choose_option", "player": player,
                DUNGEON_VENTURE_FIELD: {"schema_version": 1, "mode": VentureMode.UNDERCITY.value}},
    )


def _take_initiative(host, player: str, *, reason: str) -> str:
    host._require_seat(player, in_game=True)
    previous = host.state.initiative
    host.state.initiative = player
    details = {"player": player, "previous": previous, "reason": reason}
    changed = [player] if previous in (None, player) else [previous, player]
    host._log(player, "initiative.taken", f"{player} took the initiative.", details,
              importance=2, changed_players=changed)
    waiting = [_venture_trigger(host, player)]
    host._dispatch_semantic_event("initiative.taken", details, trigger_batch=waiting)
    enqueue_trigger_batch(host, waiting)
    return player


def commit_take_initiative(host, intent: TakeInitiativeIntent) -> str:
    item = next((row for row in host.state.stack if row.ref == intent.stack_ref), None)
    if (item is None or not item.context.get("currently_resolving")
            or initiative_effect_player(item, INHERENT_INITIATIVE_TAKE) != intent.player
            or intent.player not in host.active_seats):
        raise GameRuleError("Taking the initiative lost its live affected player")
    return _take_initiative(host, intent.player, reason=item.label)


def initiative_upkeep_triggers(host, player: str) -> tuple[StackItem, ...]:
    if player and getattr(host.state, "initiative", None) == player:
        return (_venture_trigger(host, player),)
    return ()


def initiative_combat_triggers(host, events) -> tuple[StackItem, ...]:
    holder = getattr(host.state, "initiative", None)
    if holder is None:
        return ()
    # CR 726.2 groups simultaneous damage by the creatures' controller.
    attackers = dict.fromkeys(event.source_controller for event in events
        if event.was_dealt and event.combat and event.target_kind == "player" and event.target == holder
        and "creature" in event.source_types and event.source_controller in host.active_seats)
    return tuple(_initiative_trigger(
        host, controller=holder, player=attacker, key=INHERENT_INITIATIVE_TAKE,
        label=f"The initiative — {attacker} takes the initiative",
        effect={"op": "choose_option", "player": attacker, TAKE_INITIATIVE_FIELD: {"schema_version": 1}},
    ) for attacker in attackers)


def transfer_departing_initiative(host, active_before: str | None) -> None:
    holder = host.state.initiative
    if holder is None or holder in host.active_seats:
        return
    if not host.active_seats:
        host.state.initiative = None
        return
    successor = active_before if active_before in host.active_seats else host._next_active_after(active_before or holder)
    _take_initiative(host, successor, reason="the initiative holder left the game")
