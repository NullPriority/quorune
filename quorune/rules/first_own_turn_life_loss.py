"""Observe the first positive committed life loss of the active player."""

from dataclasses import replace

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..replacement.immutable import thaw_value


EVENT = "life.first_own_turn_loss"
CAPABILITY = "trigger.event.first_own_turn_life_loss"
MECHANIC = "trigger-event-first-own-turn-life-loss"
VARIANT = "opponent_first_own_turn_life_loss"
TEMPLATE = "fixed-counter-opponent-first-own-turn-life-loss-v1"


def first_own_turn_loss_condition():
    return {"field": "player", "op": "ne", "value": "$source.controller"}


def first_own_turn_loss_results():
    return (
        {"op": "place_counters", "card": "$source.zone_object", "counter": "+1/+1", "amount": 1, "source": "$source"},
        {"op": "draw", "player": "$controller", "count": 1, "private": True},
    )


def first_own_turn_life_loss_runtime_dependencies(program, subscriptions):
    subscribed = subscriptions is not None and subscriptions.subscription_for(EVENT) is not None
    if program.event != EVENT and CAPABILITY not in program.capability_dependencies and not subscribed:
        return ()
    if (
        program.event != EVENT or program.active_zone != "battlefield"
        or program.cost_schema is not None or program.target_schema is not None or program.handlers
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or thaw_value(program.event_condition) != first_own_turn_loss_condition()
        or tuple(thaw_value(effect) for effect in program.effects) != first_own_turn_loss_results()
    ):
        raise ValueError("First life loss requires the exact current opponent-turn counter and draw trigger")
    return (CAPABILITY,)


def first_own_turn_loss_player_before_commit(host):
    """Capture eligibility before an actual loss batch changes the sealed journal."""
    lookup = getattr(getattr(host, "semantics", None), "programs_for_event", None)
    if not callable(lookup) or not any(CAPABILITY in program.capability_dependencies for program in lookup(EVENT)):
        return None
    from ..turn_history import current_turn_history_events

    state = host.state
    player = state.active_player
    history = state.turn_history
    if (player not in state.active_seats() or state.turn_sequence < 1
            or getattr(history, "damage_life_history_version", None) != 1
            or history.turn_sequence != state.turn_sequence):
        return None
    previous = current_turn_history_events(history, turn_sequence=state.turn_sequence, kind="player_lost_life")
    return player if not any(event.target == player for event in previous) else None


def mark_first_own_turn_life_loss(records, eligible_player):
    """Retain facts on committed records across deferred publication."""
    if eligible_player is not None:
        for index, record in enumerate(records):
            if record.player == eligible_player and record.direction == "loss" and record.amount > 0:
                return (*records[:index], replace(record, first_own_turn_loss=True), *records[index + 1:])
    return records
