from __future__ import annotations

"""Authoritative selection and mutation for ordinary land-play quotas."""

from collections.abc import Mapping
from typing import Any, Literal

from ..cast_timing import type_line_has_card_type
from ..errors import GameRuleError
from ..fixed_top_exile import ResolvingExileLandPlayIntent
from ..replacement.immutable import thaw_value
from .temporary_cast_timing import (
    TemporaryCastTimingScope, consume_temporary_play_timing, has_next_creature_card_timing,
)
from ..semantic_runtime.action_permissions import (
    LAND_PLAY_ACCOUNTING_STAT,
    LAND_PLAY_ACCOUNTING_VERSION,
    USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT,
    additional_land_play_permission_slots,
    land_play_accounting,
    land_play_permission_options,
)


TURN_LAND_PLAY_ALLOWANCE_OPERATION = "grant_cast_permission"
TURN_LAND_PLAY_ALLOWANCE_FIELD = "additional_land_plays"
TURN_LAND_PLAY_ALLOWANCE_CAPABILITY = "land.play.additional.turn_duration"
TURN_LAND_PLAY_ALLOWANCE_MECHANIC = "temporary-additional-land-plays"
TURN_LAND_PLAY_ALLOWANCE_STAT = "turn_land_play_allowance"


def turn_land_plays_effect_amount(effect, *, player="$controller") -> int:
    if (
        not isinstance(effect, Mapping)
        or set(effect) != {"op", "schema_version", "player", TURN_LAND_PLAY_ALLOWANCE_FIELD}
        or effect["op"] != TURN_LAND_PLAY_ALLOWANCE_OPERATION
        or type(effect["schema_version"]) is not int
        or effect["schema_version"] != 1
        or effect["player"] != player
        or type(effect[TURN_LAND_PLAY_ALLOWANCE_FIELD]) is not int
        or effect[TURN_LAND_PLAY_ALLOWANCE_FIELD] not in {1, 2, 3}
    ):
        raise ValueError("Additional land plays require a fixed controller allowance")
    return effect[TURN_LAND_PLAY_ALLOWANCE_FIELD]


def turn_land_plays_node_capabilities(*, effects, target_schema, mechanic_ids, **_unused):
    if target_schema is not None or len(effects) != 1 or TURN_LAND_PLAY_ALLOWANCE_MECHANIC not in mechanic_ids:
        return ()
    try:
        turn_land_plays_effect_amount(effects[0])
    except ValueError:
        return ()
    return (TURN_LAND_PLAY_ALLOWANCE_CAPABILITY,)


def is_closed_turn_land_plays_program(program):
    required = turn_land_plays_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)


def additional_land_plays_this_turn(stats, turn_sequence) -> int:
    if TURN_LAND_PLAY_ALLOWANCE_STAT not in stats:
        return 0
    entry = stats[TURN_LAND_PLAY_ALLOWANCE_STAT]
    if (
        not isinstance(entry, Mapping)
        or set(entry) != {"version", "turn", "amount"}
        or type(entry["version"]) is not int
        or entry["version"] != 1
        or type(entry["turn"]) is not int
        or not 0 <= entry["turn"] <= turn_sequence
        or type(entry["amount"]) is not int
        or entry["amount"] <= 0
    ):
        raise ValueError("Additional land-play allowance must be a versioned turn fact")
    return entry["amount"] if entry["turn"] == turn_sequence else 0


def grant_turn_land_plays(host, effect, *, actor, reason):
    if actor not in host.active_seats:
        raise GameRuleError("Additional land plays require the resolving controller")
    stats = host.state.players[actor].stats
    turn = host.state.turn_sequence
    try:
        amount = turn_land_plays_effect_amount(
            {key: value for key, value in effect.items() if key not in {"reason", "_runtime_source"}},
            player=actor,
        )
        total = additional_land_plays_this_turn(stats, turn) + amount
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    stats[TURN_LAND_PLAY_ALLOWANCE_STAT] = {"version": 1, "turn": turn, "amount": total}
    host._log(
        actor, "permission.additional_land_plays", f"{actor} gains {amount} additional land plays this turn.",
        {"player": actor, "amount": amount, "reason": reason}, importance=1, changed_players=[actor],
    )
    return True


def land_play_timing_window(state, player) -> Literal["ordinary", "next_creature_card"] | None:
    if player != state.active_player:
        return None
    if not state.stack and (state.phase, state.step) in {("precombat_main", "main"), ("postcombat_main", "main")}:
        return "ordinary"
    if has_next_creature_card_timing(state.players[player].stats, state.turn_sequence):
        return "next_creature_card"
    return None


def land_play_face_type_line(record, face) -> str:
    return str(face.get("type_line") or "") if face is not None else record.type_line


def land_play_timing_matches(window, card, record, face) -> bool:
    if window == "ordinary":
        return True
    type_line = land_play_face_type_line(record, face)
    return (window == "next_creature_card" and type_line_has_card_type(type_line, "land")
            and TemporaryCastTimingScope.NEXT_CREATURE_CARD.matches(type_line, physical_card=card.object_kind == "card"))


def resolving_exile_land_play_options(host, actor, card):
    return resolving_land_play_options(host, actor, card, origin="exile")


def resolving_land_play_options(host, actor, card, *, origin):
    """Offer land faces within the controller's existing play quota."""
    from ..discover_model import discover_options_fingerprint

    if (origin not in {"library", "exile"} or actor not in host.active_seats or host.state.active_player != actor
            or not land_play_permission_options(host, actor) or card.zone != origin
            or card.owner != actor or not card.is_card_object or card.face_down):
        return ()
    record = host.card_record(card)
    if record is None:
        return ()
    result = []
    for face in host._land_play_faces(record):
        face_name = str(face.get("name")) if face is not None else None
        life = host._land_entry_life_amount(record, face)
        option = {"kind": "land", "card": card.ref, "face": face_name, "requirements": {},
                  "label": f"Play {face_name or card.printed_name}", "pay_life": False}
        result.append(option)
        from ..life_state import available_life_for_payment

        if 0 < life <= available_life_for_payment(host, actor):
            result.append({**option, "label": f"{option['label']} and pay {life} life", "pay_life": True})
    prefix = "fixed-top-land:" if origin == "exile" else "revealed-library-land:"
    return tuple({**option, "id": prefix + discover_options_fingerprint((option,))} for option in result)


def resolving_land_play_response(host, actor, intent):
    if isinstance(intent, ResolvingExileLandPlayIntent):
        return resolving_exile_land_play_response(host, actor, intent)
    from .shared_type_library_play import resolving_shared_type_land_response
    return resolving_shared_type_land_response(host, actor, intent)


def resolving_exile_land_play_response(host, actor, intent):
    from ..discover_casting import require_discover_resolution

    if not isinstance(intent, ResolvingExileLandPlayIntent) or intent.actor != actor:
        raise GameRuleError("Resolving land play requires its typed controller authorization")
    require_discover_resolution(host, actor=actor, stack_ref=intent.stack_ref)
    identity = intent.card
    card = host.state.cards.get(identity.object_id)
    if card is None or card.ref != identity.ref or card.logical_object_id != identity.logical_object_id:
        raise GameRuleError("The resolving land play lost its captured exile incarnation")
    option = next((row for row in resolving_exile_land_play_options(host, actor, card)
                   if row["id"] == intent.option_id), None)
    if option is None:
        raise GameRuleError("The resolving land play is unavailable")
    return {"card": card.ref, "from": "exile", "face": option["face"], "pay_life": option["pay_life"],
            "_entry_replacement_selections": [thaw_value(value) for value in intent.replacement_selections]}


def selected_land_play_permission(
    host: Any,
    player: str,
    response: Mapping[str, Any],
) -> Any:
    options = land_play_permission_options(host, player)
    if not options:
        raise GameRuleError("No land plays remain")
    selected = str(response.get("land_play_permission") or "")
    raw_card=response.get('card') or response.get('id')
    if raw_card:
        from .source_land_plays import source_land_play_choices,SourceLandPlayChoice
        card=host._resolve_object(player,str(raw_card),zones={str(response.get('from') or 'hand')},owned_only=False)
        choices=source_land_play_choices(host,player,card)
        if not choices:raise GameRuleError('No current source permission allows this land play')
        if not selected:return choices[0]
        legacy_selections={'base',*additional_land_play_permission_slots(host,player)}
        if (selected in options or selected in legacy_selections) and any(isinstance(choice,str) for choice in choices):return selected
        match=next((choice for choice in choices if (choice.permission_id if isinstance(choice,SourceLandPlayChoice) else choice)==selected),None)
        if match is None:raise GameRuleError('Selected source land permission is stale')
        return match
    if not selected:
        return options[0]
    legacy_selections = {
        "base",
        *additional_land_play_permission_slots(host, player),
    }
    if selected not in options and selected not in legacy_selections:
        raise GameRuleError("Selected land-play permission is stale")
    return selected


def consume_land_play_permission(
    host: Any,
    player: str,
    selected: Any,
    *,
    played_card: Any = None,
    type_line: str | None = None,
) -> None:
    from .source_land_plays import SourceLandPlayChoice,commit_source_land_choice
    source_choice=selected if isinstance(selected,SourceLandPlayChoice) else None
    if source_choice is not None:selected=source_choice.numeric_permission
    selected_land_play_permission(
        host,
        player,
        {"land_play_permission": selected},
    )
    state = host.state.players[player]
    base_allowance, played = land_play_accounting(host, player)
    completed = played + 1
    state.stats[LAND_PLAY_ACCOUNTING_STAT] = {
        "version": LAND_PLAY_ACCOUNTING_VERSION,
        "base_allowance": base_allowance,
        "played": completed,
    }
    state.stats.pop(USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT, None)
    state.land_plays_remaining = max(0, base_allowance - completed)
    if source_choice is not None:commit_source_land_choice(host,player,source_choice)
    if played_card is not None and type_line is not None:
        consume_temporary_play_timing(state.stats, host.state.turn_sequence, type_line=type_line,
                                      object_kind=played_card.object_kind)


def expire_turn_land_play_allowance(stats):
    stats.pop(TURN_LAND_PLAY_ALLOWANCE_STAT, None)


def reset_additional_land_play_permissions(host: Any, player: str) -> None:
    stats = host.state.players[player].stats
    stats.pop(USED_ADDITIONAL_LAND_PLAY_SLOTS_STAT, None)
    stats.pop(LAND_PLAY_ACCOUNTING_STAT, None)
    expire_turn_land_play_allowance(stats)


__all__ = [
    "consume_land_play_permission",
    "reset_additional_land_play_permissions",
    "selected_land_play_permission",
]
