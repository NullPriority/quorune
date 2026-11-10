from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from .errors import GameRuleError
from .mana_spend_rules import paid_mana_protects_cast
from .model import StackItem
from .rules.next_cast_counter_protection import announced_cast_is_protected
from .semantic_runtime.static_cast_rules import static_stack_item_uncounterable
from .zone_trigger_events import ZoneTransitionKind, counter_destination_fields
from .combat_entry_activations import cleanup_ninjutsu_reveal
from .effect_runtime.hand_inspection import cleanup_stack_cost_hand_reveals


INTRINSIC_COUNTER_PROHIBITION_CAPABILITY = (
    "stack.counter.prohibition.intrinsic"
)


class StackCounterHost(Protocol):
    state: Any
    semantics: Any

    def move_card(self, object_id: str, zone: str, **kwargs: Any) -> Any: ...

    def _increment_optimization(self, seat: str, key: str) -> None: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Any = None,
        **kwargs: Any,
    ) -> None: ...


def oracle_has_intrinsic_counter_prohibition(
    semantics: Any,
    oracle_id: str,
    *,
    current_trusted: Callable[[Any], bool],
) -> bool:
    """Return the pinned trusted CardProgram declaration for one Oracle ID."""

    return any(current_intrinsic_counter_prohibitions(semantics, oracle_id, current_trusted=current_trusted))


def current_intrinsic_counter_prohibitions(
    semantics: Any,
    oracle_id: str,
    *,
    current_trusted: Callable[[Any], bool],
):
    for program in semantics.programs_for_oracle(oracle_id):
        if (
            program.active_zone == "stack" and program.event == "continuous"
            and INTRINSIC_COUNTER_PROHIBITION_CAPABILITY in program.capability_dependencies
            and current_trusted(program)
        ):
            yield program


def stack_item_can_be_countered(
    host: StackCounterHost,
    item: StackItem,
) -> bool:
    if item.context.get("cant_be_countered") or announced_cast_is_protected(item) or paid_mana_protects_cast(item):
        return False
    if static_stack_item_uncounterable(host, item):
        return False
    if item.kind in {"spell", "spell_copy"} and host.state.players[
        item.controller
    ].stats.get(
        "spells_cant_be_countered_until_end"
    ):
        return False
    if item.card_object_id:
        card = host.state.cards[item.card_object_id]
        if card.annotations.get("cant_be_countered"):
            return False
    return True


def _prepare_counter_destination(host, item, value, selections):
    from .entry_counters import capture_prospective_entry_characteristics
    from .semantic_runtime.zone_replacements import prepare_zone_change_replacement

    if item.kind not in {"spell", "spell_copy"}:
        raise GameRuleError("Only a countered spell can move to an alternate destination")
    zone, position = counter_destination_fields(value)
    card = host.state.cards.get(item.card_object_id or "")
    if card is None or card.zone != "stack":
        return zone, {}
    characteristics, _ = capture_prospective_entry_characteristics(
        host, card=card, enter_face=None, battlefield_entry=False,
    )
    prepared = prepare_zone_change_replacement(
        host, card, zone, entry_characteristics=characteristics,
        transition_kind=ZoneTransitionKind.COUNTERED_SPELL, selections=selections,
        error_type=GameRuleError,
    )
    return zone, {"position": position, "prepared_replacement": prepared}


def counter_stack_item(
    host: StackCounterHost,
    value: str,
    *,
    destination: str = "graveyard",
    reason: str = "countered",
    as_rule: bool = False,
    countered_by: str | None = None,
    counter_destination: str | None = None,
    replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
) -> StackItem:
    item = next(
        (
            candidate
            for candidate in host.state.stack
            if candidate.ref == value or candidate.stack_id == value
        ),
        None,
    )
    if item is None:
        raise GameRuleError(f"No stack object {value}")
    if not as_rule and not stack_item_can_be_countered(host, item):
        host._log(
            countered_by,
            "stack.counter.failed",
            f"{item.ref} {item.label} could not be countered.",
            {
                "stack": item.ref,
                "reason": reason,
                "cant_be_countered": True,
            },
            importance=2,
        )
        return item
    move_options = {}
    if counter_destination is not None:
        destination, move_options = _prepare_counter_destination(
            host, item, counter_destination, replacement_selections,
        )
    from .rules.event_spell_copy import pin_event_spell_copy_departure
    pin_event_spell_copy_departure(host,item)
    host.state.stack.remove(item)
    cleanup_ninjutsu_reveal(host, item)
    cleanup_stack_cost_hand_reveals(host,item)
    if item.card_object_id:
        card = host.state.cards[item.card_object_id]
        if card.zone == "stack":
            moved = host.move_card(
                card.object_id,
                destination,
                reason=reason,
                log=False,
                semantic_events=True,
                transition_kind=ZoneTransitionKind.COUNTERED_SPELL,
                **move_options,
            )
            if counter_destination is not None:
                destination = moved.zone
    telemetry_seat = (
        countered_by
        if countered_by in host.state.players
        else item.controller
    )
    host._increment_optimization(
        telemetry_seat,
        (
            "spells_countered_by_rules"
            if as_rule
            else "spells_countered_by_effect"
        ),
    )
    host._log(
        countered_by,
        "stack.counter",
        f"{item.ref} {item.label} was countered.",
        {
            "stack": item.ref,
            "destination": destination,
            "reason": reason,
            "counter_kind": "rules" if as_rule else "effect",
        },
        importance=2,
    )
    return item


__all__ = [
    "INTRINSIC_COUNTER_PROHIBITION_CAPABILITY",
    "StackCounterHost",
    "counter_stack_item",
    "current_intrinsic_counter_prohibitions",
    "oracle_has_intrinsic_counter_prohibition",
    "stack_item_can_be_countered",
]
