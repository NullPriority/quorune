from __future__ import annotations

from typing import Any, Mapping

from ..counter_placement import CounterPlacementError, place_counters_on_refs
from ..errors import GameRuleError
from ..effect_contracts import effect_family_contract
from ..model import GoadDesignation
from ..trigger_processing import schedule_delayed_trigger


OPERATIONS = effect_family_contract("state-and-permissions.v1").operations
_REASON_FIELD = "reason"


def _apply_goad(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    host._require_seat(actor, in_game=True)
    card = host._resolve_object(
        actor,
        str(effect["card"]),
        zones={"battlefield"},
    )
    card_types, _, _ = host._type_parts(
        str(host._effective_card_data(card).get("type_line") or "")
    )
    if "creature" not in card_types:
        raise GameRuleError("Only a creature can be goaded")
    prohibition = host._goad_prohibition_source(card)
    if prohibition is not None:
        host._log(
            actor,
            "permanent.goad.prevented",
            f"{card.ref} can't be goaded.",
            {
                "object": card.ref,
                "player": actor,
                "source": prohibition.ref,
                "reason": reason,
            },
            importance=1,
        )
        return card.ref
    existing = next(
        (
            designation
            for designation in host._active_goad_designations(card)
            if designation.player == actor
        ),
        None,
    )
    if existing is not None:
        host._log(
            actor,
            "permanent.goad.redundant",
            f"{card.ref} was already goaded by {actor}.",
            {
                "object": card.ref,
                "player": actor,
                "reason": reason,
            },
            importance=1,
        )
        return card.ref
    designation = GoadDesignation(
        player=actor,
        expires_at_turns_begun=(
            host.state.players[actor].turns_begun + 1
        ),
        created_turn_sequence=host.state.turn_sequence,
    )
    card.goaded_by.append(designation)
    host._log(
        actor,
        "permanent.goad",
        f"{card.ref} was goaded by {actor}.",
        {
            "object": card.ref,
            "player": actor,
            "expires_at_turns_begun": (
                designation.expires_at_turns_begun
            ),
            _REASON_FIELD: reason,
        },
        importance=2,
        changed_objects=[card.object_id],
    )
    return card.ref



def _apply_next_spell_improvise_or_next_spell_uncounterable(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    host.state.players[seat].stats[op] = True
    host._log(
        actor,
        "effect.next_spell",
        (
            f"{seat}'s next spell has "
            + (
                "improvise."
                if op == "next_spell_improvise"
                else "can't be countered."
            )
        ),
        {"player": seat, "effect": op, "reason": reason},
        importance=1,
        changed_players=[seat],
    )
    return True



def _apply_veil_of_summer(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    opponent_cast_blue_or_black = any(
        event.turn_sequence == host.state.turn_sequence
        and event.code == "stack.cast"
        and event.actor in host.active_seats
        and event.actor != seat
        and {"U", "B"}.intersection(
            {
                str(value).upper()
                for value in event.details.get("colors", [])
            }
        )
        for event in host.state.events
    )
    if opponent_cast_blue_or_black:
        host._begin_draw_sequence(seat, 1, reason=reason)
    return _apply_grant_uncounterable_hexproof_from_colors_until_end(
        host,
        {"player": seat, "colors": ["U", "B"]},
        actor=actor,
        operation="grant_uncounterable_hexproof_from_colors_until_end",
        reason=reason,
        legacy_label="legacy opponent-color protection effect",
        drew=opponent_cast_blue_or_black,
    )


def _apply_grant_uncounterable_hexproof_from_colors_until_end(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
    legacy_label: str | None = None,
    drew: bool | None = None,
) -> Any:
    seat = str(effect.get("player") or actor)
    host._require_seat(seat, in_game=True)
    raw_colors = effect.get("colors")
    if not isinstance(raw_colors, (list, tuple)) or not raw_colors:
        raise GameRuleError("Temporary hexproof colors must be a nonempty list")
    colors = [str(value).upper() for value in raw_colors]
    if len(colors) != len(set(colors)) or any(
        color not in {"W", "U", "B", "R", "G"} for color in colors
    ):
        raise GameRuleError("Temporary hexproof colors must be unique Magic colors")
    player = host.state.players[seat]
    player.stats["spells_cant_be_countered_until_end"] = True
    player.stats["hexproof_from_colors_until_end"] = colors
    host._log(
        actor,
        "effect.temporary_spell_and_hexproof_permissions",
        (
            f"{seat} gained {legacy_label}'s turn-long effects."
            if legacy_label
            else (
                f"{seat}'s spells can't be countered and {seat} gained "
                f"hexproof from {', '.join(colors)} until end of turn."
            )
        ),
        {
            "player": seat,
            "colors": colors,
            _REASON_FIELD: reason,
            **({"drew": drew} if drew is not None else {}),
        },
        importance=2,
        changed_players=[seat],
    )
    return True



def _apply_add_counter_selected(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    counter = str(effect.get("counter") or "").strip()
    amount = int(effect.get("amount", 1))
    if not counter or amount < 0:
        raise GameRuleError("Counter effect requires a name and nonnegative amount")
    try:
        results = place_counters_on_refs(
            host,
            actor=actor,
            object_refs=tuple(
                str(value) for value in effect.get("cards") or ()
            ),
            counter_name=counter,
            amount=amount,
            selections=tuple(
                effect.get("_replacement_selections") or ()
            ),
            reason=reason,
            source_ref=str(effect.get("source") or "") or None,
        )
    except CounterPlacementError as exc:
        raise GameRuleError(str(exc)) from exc
    return [
        host.state.cards[result.object_id].ref
        for result in results
    ]



def _apply_mana(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    color = str(effect.get("color") or "C").upper()
    amount = int(effect.get("amount", 1))
    if color not in "WUBRGC" or len(color) != 1 or amount < 0:
        raise GameRuleError("Invalid semantic mana effect")
    source_ref = str(effect.get("source") or "")
    host._add_mana_to_pool(
        seat,
        {color: amount},
        snow_source=(
            bool(source_ref)
            and bool(host._mana_source_is_snow(source_ref))
        ),
    )
    host._log(
        actor,
        "mana.semantic",
        f"{seat} added {amount} {color}.",
        {"bundle": {color: amount}, "reason": reason},
        importance=1,
        changed_players=[seat],
    )
    return amount



def _apply_delayed_mana(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    amount = int(effect.get("amount", 0))
    return schedule_delayed_trigger(
        host,
        controller=seat,
        label=str(effect.get("label") or "Delayed mana"),
        event_kind="step.begin",
        condition={
            "player": seat,
            "phase": ["precombat_main", "postcombat_main"],
            "step": "main",
        },
        stack_template={
            "label": str(effect.get("label") or "Delayed mana"),
            "context": {
                "dynamic_effects": [
                    {
                        "op": "mana",
                        "player": seat,
                        "color": str(effect.get("color") or "C"),
                        "amount": amount,
                    }
                ]
            },
        },
        once=True,
    ).ref



def _apply_delayed_pact_payment(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    cost = dict(effect.get("cost") or {})
    return schedule_delayed_trigger(
        host,
        controller=seat,
        label=str(
            effect.get("label")
            or "Pact of Negation delayed payment"
        ),
        event_kind="step.begin",
        condition={
            "player": seat,
            "phase": "beginning",
            "step": "upkeep",
            "after_turn_sequence": host.state.turn_sequence,
        },
        stack_template={
            "label": str(
                effect.get("label")
                or "Pact of Negation delayed payment"
            ),
            "context": {
                "dynamic_effects": [
                    {
                        "op": "pay_or_lose",
                        "player": seat,
                        "cost": cost,
                    }
                ]
            },
        },
        once=True,
    ).ref


HANDLERS = {
    'add_counter_selected': _apply_add_counter_selected,
    'delayed_mana': _apply_delayed_mana,
    'delayed_pact_payment': _apply_delayed_pact_payment,
    'goad': _apply_goad,
    'grant_uncounterable_hexproof_from_colors_until_end': _apply_grant_uncounterable_hexproof_from_colors_until_end,
    'mana': _apply_mana,
    'next_spell_improvise': _apply_next_spell_improvise_or_next_spell_uncounterable,
    'next_spell_uncounterable': _apply_next_spell_improvise_or_next_spell_uncounterable,
    'veil_of_summer': _apply_veil_of_summer,
}


def apply_effect(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    handler = HANDLERS.get(operation)
    if handler is None:
        raise GameRuleError(
            f"Unsupported owned effect {operation!r}"
        )
    return handler(
        host,
        effect,
        actor=actor,
        operation=operation,
        reason=reason,
    )
