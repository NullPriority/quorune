from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol

from .counter_removal import (
    commit_counter_removals,
    CounterRemoval,
    CounterRemovalError,
    plan_counter_removals,
)
from .control_effects import record_source_transition, synchronize_control_effects

VIGILANCE_KEYWORD = "vigilance"
STUN_COUNTER_NAME = "stun"
REASON_FIELD = "reason"
NEXT_UNTAP_PROHIBITION_ANNOTATION = "does_not_untap_next"


class TapStateError(ValueError):
    """A requested canonical tap-state transition is malformed."""


def consume_next_untap_prohibition(card: Any) -> bool:
    """Expire one object-local prohibition at its next physical untap step."""

    annotations = card.annotations
    if not isinstance(annotations, dict):
        raise TapStateError("Permanent annotations must be a mapping")
    value = annotations.pop(NEXT_UNTAP_PROHIBITION_ANNOTATION, False)
    if type(value) is not bool:
        raise TapStateError(
            "Next-untap prohibition state must be boolean"
        )
    return value


class TapStateHost(Protocol):
    """Transitional mutation port exposed by the authoritative rules host."""

    state: Any

    @property
    def active_seats(self) -> list[str]: ...

    def _resolve_object(
        self, actor: str, ref: str, *, zones: set[str]
    ) -> Any: ...

    def _effective_card_data(self, card: Any) -> dict[str, Any]: ...

    def _dispatch_semantic_event(
        self, event: str, context: Mapping[str, Any], **kwargs: Any,
    ) -> Any: ...

    def _semantic_event_sources(self, *, zones: set[str] | None = None) -> list[Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_objects: list[str] | None = None,
        changed_players: list[str] | None = None,
    ) -> Any: ...


def tap_declared_attackers(
    host: TapStateHost,
    attackers: Iterable[Any],
) -> list[str]:
    """Apply CR 508.1f using the current effective keyword snapshot.

    The declaration coordinator has already established legality. This owner
    preflights the complete supplied set before mutation so a malformed entry
    cannot leave an earlier attacker tapped. Vigilance is a redundant static
    ability: one or several current instances produce the same no-tap result.
    """

    prepared: list[tuple[Any, bool]] = []
    seen: set[str] = set()
    for card in tuple(attackers):
        object_id = str(getattr(card, "object_id", ""))
        object_ref = str(getattr(card, "ref", ""))
        if not object_id or not object_ref:
            raise TapStateError("Declared attacker identity is required")
        if object_id in seen:
            raise TapStateError("A declared attacker may appear only once")
        seen.add(object_id)
        if getattr(card, "zone", None) != "battlefield":
            raise TapStateError("Declared attacker must be on the battlefield")
        if type(getattr(card, "tapped", None)) is not bool:
            raise TapStateError("Declared attacker tap state must be boolean")
        if card.tapped:
            raise TapStateError("A tapped permanent cannot be newly declared")
        data = host._effective_card_data(card)
        raw_keywords = data.get("keywords", ())
        if not isinstance(raw_keywords, (list, tuple, set, frozenset)) or any(
            not isinstance(keyword, str) for keyword in raw_keywords
        ):
            raise TapStateError("Effective attacker keywords are malformed")
        keywords = {keyword.casefold() for keyword in raw_keywords}
        prepared.append((card, VIGILANCE_KEYWORD not in keywords))

    tapped_refs: list[str] = []
    for card, should_tap in prepared:
        if should_tap:
            card.tapped = True
            record_source_transition(host, card, previous_tapped=False)
            tapped_refs.append(card.ref)
    pending: list[Any] = []
    for card, should_tap in prepared:
        if should_tap:
            dispatch_tap_state_occurrence(
                host, card, tapped=True, reason="attack declaration",
                declared_attacker=True, trigger_batch=pending,
            )
    if pending:
        from .trigger_processing import enqueue_trigger_batch
        enqueue_trigger_batch(host, pending)
    return tapped_refs


def untap_permanent(
    host: TapStateHost,
    card: Any,
    *,
    actor: str | None,
    reason: str,
) -> bool:
    """Apply one untap or the mandatory stun-counter replacement."""

    if type(card.tapped) is not bool:
        raise TapStateError("Permanent tap state must be boolean")
    if not card.tapped:
        return False
    stun_count = card.counters.get(STUN_COUNTER_NAME, 0)
    if type(stun_count) is not int:
        raise TapStateError("Stun-counter state is malformed")
    if stun_count < 0:
        raise TapStateError("Stun-counter state cannot be negative")
    if stun_count:
        try:
            plan = plan_counter_removals(
                host,
                (
                    CounterRemoval(
                        object_id=card.object_id,
                        counter_name=STUN_COUNTER_NAME,
                        amount=1,
                        expected_logical_object_id=(
                            card.logical_object_id
                        ),
                    ),
                ),
            )
            commit_counter_removals(host, plan)
        except CounterRemovalError as exc:
            raise TapStateError(str(exc)) from exc
        host._log(
            actor,
            "permanent.untap.replaced",
            (
                "A stun counter was removed from "
                f"{card.ref} instead of untapping it."
            ),
            {
                "object": card.ref,
                "counter": STUN_COUNTER_NAME,
                REASON_FIELD: reason,
            },
            importance=1,
            changed_objects=[card.object_id],
            changed_players=[card.controller],
        )
        return False
    card.tapped = False
    record_source_transition(host, card, previous_tapped=True)
    return True


def set_permanent_tapped(
    host: TapStateHost,
    object_ref: str,
    *,
    actor: str,
    tapped: bool,
    reason: str,
    logical_object_id: str | None = None,
    revert: bool = False,
    log: bool = True,
    semantic_events: bool = True,
    untap_cost: bool = False,
) -> str:
    """Commit one validated tap-state intent through authoritative state."""

    card = next(
        (
            candidate
            for candidate in host.state.cards.values()
            if candidate.ref == object_ref
        ),
        None,
    )
    if card is None:
        card = host._resolve_object(
            actor,
            object_ref,
            zones={"battlefield"},
        )
    if (
        logical_object_id is not None
        and card.logical_object_id != logical_object_id
    ):
        return card.ref
    if card.zone != "battlefield":
        return card.ref
    previous_tapped = card.tapped
    if tapped:
        changed = not card.tapped
        card.tapped = True
    elif revert or untap_cost:
        changed = card.tapped
        card.tapped = False
    else:
        changed = untap_permanent(
            host,
            card,
            actor=actor,
            reason=reason,
        )
    if changed and not revert and (tapped or untap_cost):
        record_source_transition(host, card, previous_tapped=previous_tapped)
    if changed and not revert and semantic_events:
        synchronize_control_effects(host, reason="source tap state changed")
        dispatch_tap_state_occurrence(host, card, tapped=tapped, reason=reason)
    if changed and log:
        operation = "tap" if tapped else "untap"
        host._log(
            actor,
            f"permanent.{operation}",
            f"{card.ref} was {operation}ped.",
            dict(object=card.ref, reason=reason),
            importance=1,
            changed_objects=[card.object_id],
        )
    return card.ref


def dispatch_tap_state_occurrence(
    host: TapStateHost, card: Any, *, tapped: bool, reason: str,
    declared_attacker: bool = False, trigger_batch: list[Any] | None = None,
) -> None:
    """Publish an actual committed transition through the shared trigger owner.

    Entry state and rollback do not call this boundary. Coordinated groups
    commit every member before invoking it so discovery sees the whole event.
    """

    version = getattr(host.state, "tap_state_event_version", None)
    if version is None:
        return
    if type(version) is not int or version != 1:
        raise TapStateError("Unsupported tap-state occurrence version")
    context = tap_state_occurrence_context(
        host, card, reason=reason, declared_attacker=declared_attacker,
    )
    from .trigger_processing import collect_trigger_items, enqueue_trigger_batch
    pending = collect_trigger_items(
        host, "permanent.tap" if tapped else "permanent.untap", context,
        sources=sorted(host._semantic_event_sources(), key=lambda source: source.object_id),
    )
    if trigger_batch is not None:
        trigger_batch.extend(pending)
    elif pending:
        enqueue_trigger_batch(host, pending)


def tap_state_occurrence_context(
    host: TapStateHost, card: Any, *, reason: str, declared_attacker: bool = False,
) -> dict[str, Any]:
    """Seal one committed member for semantic and delayed trigger collectors."""
    data = host._effective_card_data(card)
    types, subtypes, supertypes = host._type_parts(str(data.get("type_line") or ""))
    return {
        "card": card.ref,
        "card_object_id": card.object_id,
        "card_object_identity": card.logical_object_id,
        "controller": card.controller,
        "owner": card.owner,
        "types": sorted(types),
        "subtypes": sorted(subtypes),
        "supertypes": sorted(supertypes),
        "colors": list(data.get("colors", ())),
        "keywords": list(data.get("keywords", ())),
        "token": card.is_token,
        "active_player": host.state.active_player,
        "player": host.state.active_player,
        "phase": host.state.phase,
        "step": host.state.step,
        "declared_attacker": declared_attacker,
        REASON_FIELD: reason,
    }
def dispatch_tapped_cost_group(
    host: TapStateHost, cards: Iterable[Any], *, reason: str,
) -> None:
    """Discover a simultaneous tap cost only after its full group commits."""

    dispatch_tap_state_group(host, cards, tapped=True, reason=reason)


def dispatch_tap_state_group(
    host: TapStateHost, cards: Iterable[Any], *, tapped: bool, reason: str,
) -> None:
    """Announce an explicitly simultaneous instruction's committed members."""

    synchronize_control_effects(host, reason="simultaneous source tap states changed")
    pending: list[Any] = []
    for card in sorted(cards, key=lambda value: value.object_id):
        dispatch_tap_state_occurrence(
            host, card, tapped=tapped, reason=reason, trigger_batch=pending,
        )
    if pending:
        from .trigger_processing import enqueue_trigger_batch
        enqueue_trigger_batch(host, pending)


def untap_all_creatures(
    host: TapStateHost, *, actor: str, reason: str
) -> list[str]:
    """Commit the represented phased-in effective-creature untap set."""

    changed: list[str] = []
    for seat in host.active_seats:
        for object_id in host.state.players[seat].zones["battlefield"]:
            card = host.state.cards[object_id]
            card_types = host._type_parts(
                str(host._effective_card_data(card).get("type_line") or "")
            )[0]
            if card.phased_out or "creature" not in card_types:
                continue
            if untap_permanent(
                host, card, actor=actor, reason=reason
            ):
                changed.append(object_id)
    if changed:
        pending: list[Any] = []
        for object_id in changed:
            dispatch_tap_state_occurrence(
                host, host.state.cards[object_id], tapped=False,
                reason=reason, trigger_batch=pending,
            )
        if pending:
            from .trigger_processing import enqueue_trigger_batch
            enqueue_trigger_batch(host, pending)
        host._log(
            actor,
            "permanent.untap",
            f"Untapped {len(changed)} creature(s).",
            dict(
                objects=[
                    host.state.cards[object_id].ref
                    for object_id in changed
                ],
                reason=reason,
            ),
            importance=2,
            changed_objects=changed,
        )
    return [host.state.cards[object_id].ref for object_id in changed]
