from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from .errors import GameRuleError
from .model import StackItem
from .semantic_runtime.static_cast_rules import static_stack_item_uncounterable
from .zone_trigger_events import ZoneTransitionKind


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

    return any(
        current_trusted(program)
        and program.active_zone == "stack"
        and program.event == "continuous"
        and INTRINSIC_COUNTER_PROHIBITION_CAPABILITY
        in program.capability_dependencies
        for program in semantics.programs_for_oracle(oracle_id)
    )


def stack_item_can_be_countered(
    host: StackCounterHost,
    item: StackItem,
) -> bool:
    if item.context.get("cant_be_countered"):
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


def counter_stack_item(
    host: StackCounterHost,
    value: str,
    *,
    destination: str = "graveyard",
    reason: str = "countered",
    as_rule: bool = False,
    countered_by: str | None = None,
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
    host.state.stack.remove(item)
    if item.card_object_id:
        card = host.state.cards[item.card_object_id]
        if card.zone == "stack":
            host.move_card(
                card.object_id,
                destination,
                reason=reason,
                log=False,
                semantic_events=True,
                transition_kind=ZoneTransitionKind.COUNTERED_SPELL,
            )
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
    "oracle_has_intrinsic_counter_prohibition",
    "stack_item_can_be_countered",
]
