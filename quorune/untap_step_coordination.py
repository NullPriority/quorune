from __future__ import annotations

"""Imperative CR 502 shell over the immutable participation planner."""

from typing import Any, Mapping, Protocol, Sequence

from .model import CardInstance, StackItem
from .day_night import apply_untap_day_night_transition
from .semantic_runtime.untap_steps import current_untap_step_plan
from .tap_state import (
    consume_next_untap_prohibition,
    REASON_FIELD,
    untap_permanent,
)
from .trigger_processing import collect_trigger_items


class UntapStepCoordinationHost(Protocol):
    state: Any
    semantics: Any
    active_seats: list[str]

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _pause_for_unsupported_semantic(
        self,
        *,
        event: str,
        source: CardInstance,
    ) -> None: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: Sequence[str] = (),
        changed_players: Sequence[str] = (),
    ) -> Any: ...

    def _advance_step(
        self,
        *,
        held_triggers: Sequence[StackItem] = (),
    ) -> None: ...


def unsupported_phasing_source(
    host: UntapStepCoordinationHost,
    active_player: str,
) -> CardInstance | None:
    """Return a CR 502.1 source that the current state cannot phase safely."""

    for object_id in host.state.players[active_player].zones["battlefield"]:
        card = host.state.cards[object_id]
        if card.controller != active_player:
            continue
        keywords = {
            str(value).casefold()
            for value in host._effective_card_data(card).get("keywords", [])
        }
        if card.phased_out or "phasing" in keywords:
            return card
    return None


def coordinate_untap_step(
    host: UntapStepCoordinationHost,
    *,
    phase: str,
    step: str,
    active_player: str,
    held_triggers: Sequence[StackItem] = (),
) -> None:
    """Plan and commit one no-priority untap step through typed owners."""

    phasing_source = unsupported_phasing_source(host, active_player)
    if phasing_source is not None:
        host._pause_for_unsupported_semantic(
            event="untap.phasing",
            source=phasing_source,
        )
        return
    day_night_triggers = list(held_triggers)
    apply_untap_day_night_transition(
        host,
        trigger_batch=day_night_triggers,
    )
    plan = current_untap_step_plan(host, active_player)
    if plan.unsupported_source_object_id is not None:
        host._pause_for_unsupported_semantic(
            event="untap.selection_restriction",
            source=host.state.cards[plan.unsupported_source_object_id],
        )
        return

    untap_context = {
        "phase": phase,
        "step": step,
        "player": active_player,
    }
    waiting_triggers = collect_trigger_items(
        host,
        "step.begin",
        untap_context,
        held_triggers=day_night_triggers,
    )
    untapped_object_ids: list[str] = []
    if host.state.config.auto_untap:
        prohibited = set(plan.prohibited_object_ids)
        changed: list[str] = []
        for object_id in list(
            host.state.players[active_player].zones["battlefield"]
        ):
            card = host.state.cards[object_id]
            if card.controller != active_player or card.phased_out:
                continue
            # A next-untap prohibition expires at this physical untap step
            # even when another prohibition independently keeps it tapped.
            if consume_next_untap_prohibition(card):
                continue
            if object_id in prohibited:
                continue
            if untap_permanent(
                host,
                card,
                actor=active_player,
                reason="untap step",
            ):
                changed.append(object_id)
                untapped_object_ids.append(object_id)
        if changed:
            host._log(
                active_player,
                "permanent.untap",
                f"{active_player} untapped {len(changed)} permanent(s).",
                {
                    "objects": [
                        host.state.cards[object_id].ref
                        for object_id in changed
                    ]
                },
                importance=0,
                changed_objects=changed,
                changed_players=[active_player],
            )

        additional = set(plan.additional_object_ids)
        for seat in host.active_seats:
            if seat == active_player:
                continue
            extra_changed: list[str] = []
            for object_id in list(
                host.state.players[seat].zones["battlefield"]
            ):
                if object_id not in additional:
                    continue
                card = host.state.cards[object_id]
                if (
                    card.controller == seat
                    and not card.phased_out
                    and untap_permanent(
                        host,
                        card,
                        actor=seat,
                        reason="static untap-step ability",
                    )
                ):
                    extra_changed.append(object_id)
                    untapped_object_ids.append(object_id)
            if extra_changed:
                host._log(
                    seat,
                    "permanent.untap",
                    (
                        f"{seat} untapped {len(extra_changed)} "
                        "permanent(s) during another player's untap step."
                    ),
                    {
                        "objects": [
                            host.state.cards[object_id].ref
                            for object_id in extra_changed
                        ],
                        REASON_FIELD: "static untap-step ability",
                        "sources": list(plan.supporting_source_refs),
                    },
                    importance=1,
                    changed_objects=extra_changed,
                    changed_players=[seat],
                )

    for object_id in untapped_object_ids:
        card = host.state.cards[object_id]
        event_context = {
            "card": card.ref,
            "player": active_player,
            "controller": card.controller,
            "phase": phase,
            "step": step,
            REASON_FIELD: "untap step",
        }
        waiting_triggers = collect_trigger_items(
            host,
            "permanent.untap",
            event_context,
            held_triggers=waiting_triggers,
        )
    host._advance_step(held_triggers=waiting_triggers)


__all__ = [
    "coordinate_untap_step",
    "unsupported_phasing_source",
    "UntapStepCoordinationHost",
]
