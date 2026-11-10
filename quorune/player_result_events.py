from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .life_change import FIRST_LIFE_GAIN_EVENT, LifeChangeRecord, first_life_gain_runtime_dependencies
from .model import StackItem
from .replacement.immutable import FrozenMap
from .trigger_processing import enqueue_trigger_batch
from .turn_history import record_first_life_gain


class PlayerResultEventError(ValueError):
    """A normalized public player-result occurrence is malformed."""


class PlayerResultEventHost(Protocol):
    def _dispatch_semantic_event(
        self,
        event: str,
        context: Mapping[str, Any],
        **kwargs: Any,
    ) -> Any: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        *,
        importance: int = 1,
        changed_players: Sequence[str] = (),
    ) -> None: ...


class LifelinkGainResult(Protocol):
    player: str
    source: str
    amount: int


class PreventionLifeGainResult(Protocol):
    kind: str
    applied_amount: int
    effect_id: str
    source_id: str
    subject: str


@dataclass(frozen=True, slots=True)
class CardDrawEvent:
    """Public facts for one committed draw without the hidden card identity."""

    player: str
    draw_ordinal: int
    in_own_draw_step: bool
    draw_step_ordinal: int | None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.player) is not str or not self.player:
            raise PlayerResultEventError("Card-draw events require a player")
        if type(self.draw_ordinal) is not int or self.draw_ordinal <= 0:
            raise PlayerResultEventError(
                "Card-draw ordinals must be positive integers"
            )
        if type(self.in_own_draw_step) is not bool:
            raise PlayerResultEventError(
                "Card-draw step membership must be a boolean"
            )
        if self.draw_step_ordinal is not None and (
            type(self.draw_step_ordinal) is not int
            or self.draw_step_ordinal <= 0
        ):
            raise PlayerResultEventError(
                "Card-draw step ordinals must be positive integers"
            )
        if self.in_own_draw_step != (self.draw_step_ordinal is not None):
            raise PlayerResultEventError(
                "Card-draw step facts must agree on draw-step membership"
            )
        if self.schema_version != 1:
            raise PlayerResultEventError(
                "Unsupported normalized card-draw event schema"
            )

    @property
    def is_second_draw(self) -> bool:
        return self.draw_ordinal == 2

    @property
    def is_first_own_draw_step_draw(self) -> bool:
        return self.in_own_draw_step and self.draw_step_ordinal == 1

    def semantic_context(self) -> FrozenMap:
        return FrozenMap(
            {
                "player": self.player,
                "draw_ordinal": self.draw_ordinal,
                "in_own_draw_step": self.in_own_draw_step,
                "draw_step_ordinal": self.draw_step_ordinal,
            }
        )


@dataclass(frozen=True, slots=True)
class LifeGainEvent:
    """Public facts for one positive replacement-resolved life event."""

    event_id: str
    player: str
    amount: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.event_id) is not str or not self.event_id:
            raise PlayerResultEventError("Life-gain events require an event ID")
        if type(self.player) is not str or not self.player:
            raise PlayerResultEventError("Life-gain events require a player")
        if type(self.amount) is not int or self.amount <= 0:
            raise PlayerResultEventError(
                "Life-gain event amounts must be positive integers"
            )
        if self.schema_version != 1:
            raise PlayerResultEventError(
                "Unsupported normalized life-gain event schema"
            )

    def semantic_context(self) -> FrozenMap:
        return FrozenMap({"player": self.player, "amount": self.amount})


def player_result_runtime_dependencies(program, subscriptions) -> tuple[str, ...]:
    """Bind the committed draw and life results used by a typed program."""
    required = first_life_gain_runtime_dependencies(program, subscriptions)
    if program.event == "card.second_draw" and program.provenance.get("template_id") in {
        "fixed-counter-public-second-draw-trigger-v1",
        "fixed-typed-effect-public-second-draw-trigger-v1",
        "fixed-counter-graveyard-self-return-v1",
        "fixed-typed-effect-graveyard-self-return-v1",
    }:
        return (*required, "trigger.event.normalized_card_draw")
    return required


def dispatch_card_draw_event(
    host: PlayerResultEventHost,
    event: CardDrawEvent,
) -> tuple[str, ...]:
    """Dispatch every public trigger occurrence derived from one draw."""

    if not isinstance(event, CardDrawEvent):
        raise PlayerResultEventError(
            "Card-draw dispatch requires a normalized event"
        )
    pending: list[StackItem] = []
    context = event.semantic_context()
    dispatched = ["card.drawn"]
    host._dispatch_semantic_event(
        "card.drawn",
        context,
        trigger_batch=pending,
    )
    if event.is_second_draw:
        dispatched.append("card.second_draw")
        host._dispatch_semantic_event(
            "card.second_draw",
            context,
            trigger_batch=pending,
        )
    if not event.is_first_own_draw_step_draw:
        dispatched.append("card.draw_except_first_draw_step")
        host._dispatch_semantic_event(
            "card.draw_except_first_draw_step",
            context,
            trigger_batch=pending,
        )
    enqueue_trigger_batch(host, pending)
    return tuple(dispatched)


def dispatch_life_gain_event(
    host: PlayerResultEventHost,
    event: LifeGainEvent,
    *,
    trigger_batch: list[StackItem] | None = None,
    sources: Sequence[Any] | None = None,
    source_zones: Mapping[str, str] | None = None,
) -> None:
    """Dispatch one normalized life gain into an existing or owned batch."""

    if not isinstance(event, LifeGainEvent):
        raise PlayerResultEventError(
            "Life-gain dispatch requires a normalized event"
        )
    owns_batch = trigger_batch is None
    pending = trigger_batch if trigger_batch is not None else []
    first = record_first_life_gain(host, event.player)
    host._dispatch_semantic_event(
        "life.gained",
        event.semantic_context(),
        sources=sources,
        source_zones=source_zones,
        trigger_batch=pending,
    )
    if first:
        # The first-gain source must be present after the gain, before later state actions.
        host._dispatch_semantic_event(FIRST_LIFE_GAIN_EVENT,
            FrozenMap({'player': event.player, 'active_player': host.state.active_player}),
            trigger_batch=pending)
    if owns_batch:
        enqueue_trigger_batch(host, pending)


def dispatch_life_gain_records(
    host: PlayerResultEventHost,
    records: Sequence[LifeChangeRecord],
) -> tuple[LifeGainEvent, ...]:
    """Dispatch all positive resolved records from one typed life batch."""

    events = tuple(
        LifeGainEvent(
            event_id=record.event_id,
            player=record.player,
            amount=record.amount,
        )
        for record in records
        if record.direction == "gain" and record.amount > 0
    )
    pending: list[StackItem] = []
    for event in events:
        dispatch_life_gain_event(host, event, trigger_batch=pending)
    enqueue_trigger_batch(host, pending)
    return events


def dispatch_lifelink_gain_events(
    host: PlayerResultEventHost,
    gains: Sequence[LifelinkGainResult],
    *,
    trigger_batch: list[StackItem],
    sources: Sequence[Any],
    source_zones: Mapping[str, str],
) -> None:
    """Log and dispatch each positive committed Lifelink result."""

    for index, gain in enumerate(gains):
        host._log(
            gain.player,
            "damage.lifelink",
            f"{gain.player} gained {gain.amount} life from {gain.source}.",
            {
                "player": gain.player,
                "source": gain.source,
                "amount": gain.amount,
            },
            importance=1,
            changed_players=[gain.player],
        )
        dispatch_life_gain_event(
            host,
            LifeGainEvent(
                event_id=f"damage.lifelink:{gain.source}:{index}",
                player=gain.player,
                amount=gain.amount,
            ),
            sources=sources,
            source_zones=source_zones,
            trigger_batch=trigger_batch,
        )


def dispatch_prevention_life_gain_event(
    host: PlayerResultEventHost,
    result: PreventionLifeGainResult,
    *,
    trigger_batch: list[StackItem],
    sources: Sequence[Any],
    source_zones: Mapping[str, str],
) -> None:
    """Dispatch one positive committed prevention-aftermath life result."""

    if result.kind != "gain_life" or result.applied_amount <= 0:
        return
    dispatch_life_gain_event(
        host,
        LifeGainEvent(
            event_id=(
                "damage.prevention.life:"
                f"{result.effect_id}:{result.source_id}"
            ),
            player=result.subject,
            amount=result.applied_amount,
        ),
        sources=sources,
        source_zones=source_zones,
        trigger_batch=trigger_batch,
    )


__all__ = [
    "CardDrawEvent",
    "LifeGainEvent",
    "PlayerResultEventError",
    "PlayerResultEventHost",
    "dispatch_card_draw_event",
    "dispatch_life_gain_event",
    "dispatch_life_gain_records",
    "dispatch_lifelink_gain_events",
    "dispatch_prevention_life_gain_event",
]
