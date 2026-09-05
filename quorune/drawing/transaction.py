from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from ..cast_timing import type_line_has_card_type
from ..model import CardInstance, GameState
from ..player_result_events import CardDrawEvent, dispatch_card_draw_event
from ..zone_trigger_events import ZoneTransitionKind
from .model import (
    DiscardDrawnCardUnlessType,
    DrawError,
    PreparedDrawEvent,
    QueuedDraw,
    RevealDrawnCard,
    RevealDrawnCardBySource,
    validate_prepared_draw,
)


_DREDGE_KIND = "dredge"
_DREDGE_REASON_PREFIX = "Dredge "
_LIBRARY_ZONE = "library"
_REASON_FIELD = "reason"


class DrawCommitHost(Protocol):
    """Narrow mutation port owned by the canonical draw transaction."""

    state: GameState

    def apnap_order(self) -> list[str]: ...

    def card_record(self, card: CardInstance) -> Any: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def move_card(
        self,
        object_id: str,
        destination: str,
        **kwargs: Any,
    ) -> CardInstance: ...

    def _move_cards_simultaneously(
        self,
        changes: Sequence[tuple[str, str]],
        *,
        reason: str,
        log: bool = False,
    ) -> list[CardInstance]: ...

    def _log(self, actor: str | None, code: str, message: str, details: Any, **kwargs: Any) -> Any: ...

    def _dispatch_semantic_event(
        self,
        event: str,
        context: dict[str, Any],
        **kwargs: Any,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class DrawCommitResult:
    """Canonical outcome of one replacement-resolved draw event."""

    kind: str
    player: str
    moved_object_ids: tuple[str, ...] = ()
    drawn_object_id: str | None = None
    result_draws: tuple[QueuedDraw, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {
            "draw",
            "empty",
            "prevented",
            "prohibited",
            "result_draws",
            _DREDGE_KIND,
        }:
            raise DrawError(f"Unsupported committed draw result {self.kind!r}")
        if type(self.player) is not str or not self.player:
            raise DrawError("Committed draw result requires a player")
        if any(
            type(value) is not str or not value
            for value in self.moved_object_ids
        ):
            raise DrawError("Committed draw object IDs must be nonempty strings")
        if self.kind == "draw":
            if (
                self.drawn_object_id is None
                or self.moved_object_ids != (self.drawn_object_id,)
            ):
                raise DrawError("An ordinary draw must identify its drawn object")
        elif self.drawn_object_id is not None:
            raise DrawError("Only an ordinary draw identifies a drawn object")
        if self.kind == "result_draws":
            if not self.result_draws:
                raise DrawError("A result-draw outcome requires queued draws")
        elif self.result_draws:
            raise DrawError("Only a result-draw outcome carries queued draws")


def _require_current_request(
    host: DrawCommitHost,
    prepared: PreparedDrawEvent,
) -> None:
    request = prepared.request
    if request.player not in host.state.players:
        raise DrawError("Draw player is no longer present")
    if len(host.state.players[request.player].zones[_LIBRARY_ZONE]) != request.library_size:
        raise DrawError("Draw library size changed before commit")


def _record_draw(
    host: DrawCommitHost,
    prepared: PreparedDrawEvent,
    object_id: str,
) -> None:
    resolution = prepared.resolution
    if resolution is None:
        raise DrawError("A pending draw cannot be recorded")
    player = host.state.players[resolution.player]
    card = host.state.cards[object_id]
    turn_key = str(host.state.turn_sequence)
    draw_tracker = player.stats.setdefault("cards_drawn_by_turn", {})
    before_count = int(draw_tracker.get(turn_key, 0))
    draw_tracker[turn_key] = before_count + 1
    in_own_draw_step = bool(
        host.state.active_player == resolution.player
        and (host.state.phase, host.state.step) == ("beginning", "draw")
    )
    draw_step_tracker = player.stats.setdefault(
        "cards_drawn_in_draw_step_by_turn", {}
    )
    before_draw_step_count = (
        int(draw_step_tracker.get(turn_key, 0)) if in_own_draw_step else 0
    )
    if in_own_draw_step:
        draw_step_tracker[turn_key] = before_draw_step_count + 1
    player.draw_history.append(
        {
            "turn_sequence": host.state.turn_sequence,
            "card": card.printed_name,
            "object": card.ref,
            _REASON_FIELD: resolution.reason,
        }
    )
    host._log(
        resolution.player,
        "card.draw",
        f"{resolution.player} drew 1 card(s).",
        {"count": 1, _REASON_FIELD: resolution.reason},
        changed_players=[resolution.player],
    )
    host._log(
        resolution.player,
        "card.draw.private",
        f"{resolution.player} drew {card.printed_name}.",
        {
            "objects": [card.ref],
            "cards": [card.printed_name],
            _REASON_FIELD: resolution.reason,
        },
        visibility=[resolution.player, "analyst"],
        importance=0 if resolution.private else 1,
        changed_objects=[object_id],
        changed_players=[resolution.player],
    )
    dispatch_card_draw_event(
        host,
        CardDrawEvent(
            player=resolution.player,
            draw_ordinal=before_count + 1,
            in_own_draw_step=in_own_draw_step,
            draw_step_ordinal=(
                before_draw_step_count + 1 if in_own_draw_step else None
            ),
        ),
    )


def _drawn_card_type_line(host: DrawCommitHost, card: CardInstance) -> str:
    record = host.card_record(card)
    if record is None:
        raise DrawError(
            "A drawn-card post-action requires pinned card characteristics"
        )
    return (
        str(record.faces[0].get("type_line") or "")
        if record.faces
        else str(record.type_line)
    )


def _apply_source_linked_reveals(
    host: DrawCommitHost,
    prepared: PreparedDrawEvent,
    card: CardInstance,
    type_line: str,
) -> None:
    """Apply CR 121.9 reveals before the drawn card enters its hand."""

    resolution = prepared.resolution
    if resolution is None:
        raise DrawError("A pending draw has no reveal-as-drawn actions")
    for action in resolution.post_draw_actions:
        if not isinstance(action, RevealDrawnCardBySource):
            continue
        source = host.state.cards.get(action.source_object_id)
        if (
            source is None
            or source.ref != action.source_ref
            or source.logical_object_id != action.source_logical_object_id
            or source.zone_change_counter
            != action.source_zone_change_counter
            or source.zone != "battlefield"
            or source.phased_out
        ):
            raise DrawError(
                "The source-linked draw reveal changed before commit"
            )
        card.revealed_to = sorted(host.state.players)
        card_types, subtypes, supertypes = host._type_parts(type_line)
        host._log(
            resolution.player,
            "card.draw.reveal",
            f"{resolution.player} revealed {card.printed_name}.",
            {
                "object": card.ref,
                "card": card.printed_name,
                "source": source.ref,
                _REASON_FIELD: resolution.reason,
            },
            importance=2,
            changed_objects=[card.object_id, source.object_id],
            changed_players=[resolution.player],
        )
        host._dispatch_semantic_event(
            "card.draw.revealed_by_source",
            {
                "player": resolution.player,
                "object": card.ref,
                "source": source.ref,
                "reveal_source_object_id": source.object_id,
                "reveal_source_logical_object_id": (
                    source.logical_object_id
                ),
                "revealed_card_types": sorted(card_types),
                "revealed_card_subtypes": sorted(subtypes),
                "revealed_card_supertypes": sorted(supertypes),
                _REASON_FIELD: resolution.reason,
            },
        )


def _apply_drawn_card_actions(
    host: DrawCommitHost,
    prepared: PreparedDrawEvent,
    object_id: str,
    type_line: str,
) -> None:
    """Apply ordered CR 121.6c actions to the exact drawn object."""

    resolution = prepared.resolution
    if resolution is None:
        raise DrawError("A pending draw has no post-draw actions")
    for action in resolution.post_draw_actions:
        current = host.state.cards.get(object_id)
        if current is None or current.zone != "hand":
            raise DrawError(
                "The specifically drawn card left its hand before its "
                "post-draw actions completed"
            )
        if isinstance(action, RevealDrawnCard):
            current.revealed_to = sorted(host.state.players)
            host._log(
                resolution.player,
                "card.draw.reveal",
                f"{resolution.player} revealed {current.printed_name}.",
                {
                    "object": current.ref,
                    "card": current.printed_name,
                    _REASON_FIELD: resolution.reason,
                },
                importance=2,
                changed_objects=[object_id],
                changed_players=[resolution.player],
            )
            continue
        if isinstance(action, RevealDrawnCardBySource):
            # CR 121.9 actions were applied before the card entered the hand.
            continue
        if isinstance(action, DiscardDrawnCardUnlessType):
            if type_line_has_card_type(type_line, action.card_type):
                continue
            host.move_card(
                object_id,
                "graveyard",
                reason="discard specifically drawn nonland card",
                semantic_events=True,
                transition_kind=ZoneTransitionKind.DISCARD,
            )
            host._log(
                resolution.player,
                "card.draw.discard",
                (
                    f"{resolution.player} discarded the specifically "
                    f"drawn {current.printed_name}."
                ),
                {
                    "object": current.ref,
                    "card": current.printed_name,
                    _REASON_FIELD: resolution.reason,
                },
                importance=2,
                changed_objects=[object_id],
                changed_players=[resolution.player],
            )
            continue
        raise DrawError("Unsupported drawn-card post-action")


def _commit_ordinary_draw(
    host: DrawCommitHost,
    prepared: PreparedDrawEvent,
) -> DrawCommitResult:
    resolution = prepared.resolution
    if resolution is None:
        raise DrawError("A pending draw cannot commit")
    player = host.state.players[resolution.player]
    if not player.zones[_LIBRARY_ZONE]:
        player.attempted_empty_draw = True
        host._log(
            resolution.player,
            "card.draw.empty",
            f"{resolution.player} attempted to draw from an empty library.",
            {_REASON_FIELD: resolution.reason},
            importance=2,
            changed_players=[resolution.player],
        )
        return DrawCommitResult(
            kind="empty",
            player=resolution.player,
        )
    object_id = player.zones[_LIBRARY_ZONE][-1]
    card = host.state.cards[object_id]
    type_line = (
        _drawn_card_type_line(host, card)
        if resolution.post_draw_actions
        else ""
    )
    _apply_source_linked_reveals(host, prepared, card, type_line)
    source_linked_reveal = any(
        isinstance(action, RevealDrawnCardBySource)
        for action in resolution.post_draw_actions
    )
    host.move_card(
        object_id,
        "hand",
        reason=resolution.reason,
        log=False,
        reveal_to=(sorted(host.state.players) if source_linked_reveal else None),
    )
    _record_draw(host, prepared, object_id)
    _apply_drawn_card_actions(host, prepared, object_id, type_line)
    return DrawCommitResult(
        kind="draw",
        player=resolution.player,
        moved_object_ids=(object_id,),
        drawn_object_id=object_id,
    )


def _commit_dredge(
    host: DrawCommitHost,
    prepared: PreparedDrawEvent,
) -> DrawCommitResult:
    resolution = prepared.resolution
    if resolution is None or resolution.kind != _DREDGE_KIND:
        raise DrawError("Dredge commit requires a closed Dredge result")
    object_id = resolution.dredge_source_object_id
    source_ref = resolution.dredge_source_ref
    incarnation = resolution.dredge_source_zone_change_counter
    mill_count = resolution.dredge_mill_count
    if (
        object_id is None
        or source_ref is None
        or incarnation is None
        or mill_count is None
    ):
        raise DrawError("Dredge result is missing source data")
    source = host.state.cards.get(object_id)
    if (
        source is None
        or source.ref != source_ref
        or source.owner != resolution.player
        or source.zone != "graveyard"
        or source.zone_change_counter != incarnation
    ):
        raise DrawError("Dredge source changed before commit")
    library = host.state.players[resolution.player].zones[_LIBRARY_ZONE]
    if len(library) < mill_count:
        raise DrawError("Dredge library became too small before commit")
    milled_ids = tuple(reversed(library[-mill_count:]))
    host._move_cards_simultaneously(
        tuple((milled_id, "graveyard") for milled_id in milled_ids),
        reason=f"{_DREDGE_REASON_PREFIX}{mill_count}",
        log=False,
    )
    host.move_card(
        source.object_id,
        "hand",
        reason=f"{_DREDGE_REASON_PREFIX}{mill_count}",
        semantic_events=True,
    )
    host._log(
        resolution.player,
        "draw.replaced.dredge",
        (
            f"{resolution.player} replaced a draw by milling {mill_count} "
            f"and returning {source.ref}."
        ),
        {
            "player": resolution.player,
            "card": source.ref,
            "mill": mill_count,
            "objects": [host.state.cards[value].ref for value in milled_ids],
            _REASON_FIELD: resolution.reason,
        },
        visibility=[resolution.player, "analyst"],
        importance=2,
        changed_objects=[source.object_id, *milled_ids],
        changed_players=[resolution.player],
    )
    return DrawCommitResult(
        kind=_DREDGE_KIND,
        player=resolution.player,
        moved_object_ids=(source.object_id,),
    )


def commit_prepared_draw_result(
    host: DrawCommitHost,
    prepared: PreparedDrawEvent,
) -> DrawCommitResult:
    """Validate and commit exactly one replacement-resolved draw event."""

    validate_prepared_draw(prepared, apnap_order=host.apnap_order())
    _require_current_request(host, prepared)
    resolution = prepared.resolution
    if resolution is None:
        raise DrawError("A pending draw cannot commit")
    if resolution.kind == "draw":
        return _commit_ordinary_draw(host, prepared)
    if resolution.kind == "prevented":
        host._log(
            resolution.player,
            "card.draw.prevented",
            f"{resolution.player}'s draw was prevented.",
            {_REASON_FIELD: resolution.reason},
            importance=1,
            changed_players=[resolution.player],
        )
        return DrawCommitResult(
            kind="prevented",
            player=resolution.player,
        )
    if resolution.kind == "prohibited":
        host._log(
            resolution.player,
            "card.draw.prohibited",
            f"{resolution.player} could not draw a card.",
            {
                _REASON_FIELD: resolution.reason,
                "prohibitions": list(resolution.prohibition_ids),
            },
            importance=1,
            changed_players=[resolution.player],
        )
        return DrawCommitResult(
            kind="prohibited",
            player=resolution.player,
        )
    if resolution.kind == "result_draws":
        host._log(
            resolution.player,
            "card.draw.replaced.result_draws",
            f"{resolution.player}'s draw was replaced with another draw instruction.",
            {
                _REASON_FIELD: resolution.reason,
                "counts": [draw.count for draw in resolution.result_draws],
            },
            importance=1,
            changed_players=[resolution.player],
        )
        return DrawCommitResult(
            kind="result_draws",
            player=resolution.player,
            result_draws=resolution.result_draws,
        )
    if resolution.kind == _DREDGE_KIND:
        return _commit_dredge(host, prepared)
    raise DrawError(f"Unsupported draw result {resolution.kind!r}")


def commit_prepared_draw(
    host: DrawCommitHost,
    prepared: PreparedDrawEvent,
) -> tuple[str, ...]:
    """Game Record v3-compatible object tuple for existing callers."""

    return commit_prepared_draw_result(host, prepared).moved_object_ids


__all__ = [
    "DrawCommitHost",
    "DrawCommitResult",
    "commit_prepared_draw",
    "commit_prepared_draw_result",
]
