"""Resolve a private discard, then its replacement draw or mill."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from ..discard_effect_origin import DiscardEffectOrigin
from ..errors import GameRuleError
from ..milling import MillRequest, commit_mill, prepare_mill
from ..replacement import FrozenMap, ReplacementEffectError
from ..replacement.immutable import thaw_value
from ..replacement.ordering import ReplacementChoiceRequired, replacement_choice_payload
from ..semantic_runtime.zone_replacements import prepare_zone_change_replacement
from ..zone_trigger_events import ZoneTransitionKind
from .continuation import DrawResume
from .model import DrawError, QueuedDraw


@dataclass(frozen=True, slots=True)
class DrawDiscardContinuation:
    draw: QueuedDraw
    after: DrawResume
    turn_sequence: int
    hand: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.draw, QueuedDraw)
            or self.draw.count != 1
            or self.draw.post_draw_actions
            or not self.draw.excluded_effect_ids
            or not isinstance(self.after, DrawResume)
            or type(self.turn_sequence) is not int
            or self.turn_sequence < 0
        ):
            raise DrawError("A discard replacement needs one sealed follow-up and its turn")
        if any(
            not isinstance(pair, tuple)
            or len(pair) != 2
            or any(type(value) is not str or not value for value in pair)
            for pair in self.hand
        ) or tuple(sorted(self.hand)) != self.hand or len({ref for ref, _ in self.hand}) != len(self.hand):
            raise DrawError("A discard replacement needs exact private hand incarnations")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DrawDiscardContinuation:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "draw", "after", "turn_sequence", "hand"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or not isinstance(value["hand"], (list, tuple))
            or any(not isinstance(pair, (list, tuple)) for pair in value["hand"])
        ):
            raise DrawError("Invalid draw-discard continuation")
        return cls(
            QueuedDraw.from_dict(value["draw"]),
            DrawResume.from_dict(value["after"]),
            value["turn_sequence"],
            tuple(tuple(pair) for pair in value["hand"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "draw": self.draw.to_dict(), "after": self.after.to_dict(),
            "turn_sequence": self.turn_sequence, "hand": [list(pair) for pair in self.hand],
        }


def _hand_cards(host, seat):
    cards = [host.state.cards[oid] for oid in host.state.players[seat].zones["hand"]]
    if any(card.zone != "hand" or card.owner != seat for card in cards):
        raise DrawError("Discard replacement hand ownership changed")
    return sorted(cards, key=lambda card: card.ref)


def _hand_identity(cards):
    return tuple((card.ref, card.logical_object_id) for card in cards)


def _validate_step(host, step):
    host._require_seat(step.draw.player, in_game=True)
    cards = _hand_cards(host, step.draw.player)
    if step.turn_sequence != host.state.turn_sequence or _hand_identity(cards) != step.hand:
        raise DrawError("The discard replacement hand or turn changed")
    return cards


def begin_discard_replacement(host, draw: QueuedDraw, after: DrawResume) -> bool:
    """Return true when a replacement decision suspends the original draw."""
    cards = _hand_cards(host, draw.player)
    step = DrawDiscardContinuation(draw, after, host.state.turn_sequence, _hand_identity(cards))
    if not cards:
        return _attempt_movement(host, step, "mill", None) is None
    host.permissions.issue(
        kind="draw.discard", role="pilot", actors=[draw.player], allowed_actions=["choose"],
        payload_by_actor={draw.player: {
            "prompt": "Discard one card before drawing.",
            "cards": [{"id": card.ref, "name": card.printed_name} for card in cards],
            "legal_actions": [{
                "id": "choose", "action": "choose",
                "choice_schema": {"card": {"type": "object_ref", "required": True,
                                           "legal_refs": [card.ref for card in cards]}},
            }],
        }},
        continuation=step.to_dict(),
    )
    return True


def complete_draw_discard_choice(host, decision) -> None:
    step = DrawDiscardContinuation.from_dict(decision.continuation)
    seat = step.draw.player
    if decision.actors != [seat]:
        raise DrawError("Draw-discard choice has the wrong affected player")
    selected = decision.responses[seat].get("card")
    if type(selected) is not str or selected not in dict(step.hand):
        raise DrawError("Choose one card from the offered hand")
    _complete_attempt(host, step, "discard", selected)


def _discard_origin(host, step):
    """A replacement retains the source of the original resolving draw."""
    if getattr(host.state, "discard_effect_origin_version", None) != 1:
        return None
    final = step.after
    while final.kind in {"draw_batch", "draw_remainder"}:
        final = final.after or DrawResume.none()
    if final.kind != "semantic_resolution":
        return None
    item = next((value for value in host.state.stack if value.ref == final.stack_ref), None)
    if item is None:
        raise DrawError("The resolving draw source changed")
    if item.kind in {"spell", "spell_copy", "activated_ability", "triggered_ability"}:
        return DiscardEffectOrigin.from_resolving_stack(item)
    return None


def _attempt_movement(host, step, action, selected, selections=()):
    cards = _validate_step(host, step)
    seat = step.draw.player
    try:
        if action == "discard":
            card = next((value for value in cards if value.ref == selected), None)
            if card is None:
                raise DrawError("The selected discard incarnation changed")
            previous = card.logical_object_id
            moved = host.move_card(
                card.object_id, "graveyard", reason=step.draw.reason, semantic_events=True,
                transition_kind=ZoneTransitionKind.DISCARD, replacement_selections=selections,
                discard_effect_origin=_discard_origin(host, step),
            )
            if host.state.turn_history is None:
                return moved.zone != "hand"
            return any(
                entry.actor == seat and entry.object_incarnation == previous
                for entry in host._current_turn_history("card_discarded")
            )
        plan = prepare_mill(host, MillRequest(seat, seat, 1, step.draw.reason))
        commit_mill(host, plan, replacement_selections=selections)
        return False
    except ReplacementChoiceRequired as required:
        _issue_movement_replacement(host, step, action, selected, selections, required)
        return None


def _complete_attempt(host, step, action, selected, selections=()):
    from .coordinator import begin_draw_batch, resume_after_draw

    discarded = _attempt_movement(host, step, action, selected, selections)
    if discarded is None:
        return
    if discarded:
        begin_draw_batch(host, (step.draw,), continuation=step.after.to_dict())
        return
    if action == "discard" and _attempt_movement(host, step, "mill", selected) is None:
        return
    resume_after_draw(host, step.after)


def _issue_movement_replacement(host, step, action, selected, selections, required):
    chooser = required.pending.choice.chooser
    host.permissions.issue(
        kind="replacement.order", role="pilot", actors=[chooser], allowed_actions=["choose"],
        payload_by_actor={chooser: replacement_choice_payload(required.pending, required.effects)},
        continuation={
            "replacement_resume_kind": "draw_discard_or_mill",
            "draw_discard": step.to_dict(),
            "selected_card": {"action": action, "ref": selected},
            "replacement_selections": list(selections),
            "replacement_batch": required.batch.to_dict(),
            "replacement_effects": [effect.to_dict() for effect in required.effects],
        },
    )


def decode_draw_discard_replacement(continuation_type, value, batch, effects):
    from ..replacement.replay import _decode_combat_selections

    try:
        step = DrawDiscardContinuation.from_dict(value["draw_discard"])
        picked = value["selected_card"]
        if not isinstance(picked, Mapping) or set(picked) != {"action", "ref"}:
            raise DrawError("Invalid discard or mill movement selection")
        action, selected = picked["action"], picked["ref"]
        if (
            action not in {"discard", "mill"}
            or (selected is None and (action == "discard" or step.hand))
            or (selected is not None and (type(selected) is not str or selected not in dict(step.hand)))
        ):
            raise DrawError("Invalid selected discard source")
        if not batch.events or len(batch.events) != 1:
            raise DrawError("Discard or mill movement needs exactly one replacement event")
        event, = batch.events
        if (
            event.kind != "zone.change"
            or event.payload.get("origin") != ("hand" if action == "discard" else "library")
            or event.payload.get("destination") != "graveyard"
        ):
            raise DrawError("The movement replacement is outside the selected discard or mill")
    except (TypeError, ValueError, KeyError) as exc:
        raise ReplacementEffectError(str(exc)) from exc
    return continuation_type(
        batch=batch, effects=effects, resume_kind="draw_discard_or_mill",
        effect=FrozenMap({"draw_discard": step.to_dict(), "selected_card": dict(picked)}),
        replacement_selections=_decode_combat_selections(value),
    )


def _same_movement_choice(current, restored):
    # The zone owner rebuilds its batch and event IDs at the new revision.
    if len(current.batch.events) != 1 or current.effects != restored.effects:
        return False
    event, = current.batch.events
    sealed, = restored.batch.events
    if event.children or sealed.children:
        return False
    return replace(
        current.batch,
        batch_id=restored.batch.batch_id,
        events=(replace(event, event_id=sealed.event_id),),
        journal=tuple(replace(entry, event_id=sealed.event_id) for entry in current.batch.journal),
    ) == restored.batch


def resume_draw_discard_replacement(host, restored, selection, *, error_type):
    values = restored.thaw_effect()
    step = DrawDiscardContinuation.from_dict(values["draw_discard"])
    choice = values["selected_card"]
    action, selected = choice["action"], choice["ref"]
    selections = tuple(thaw_value(item) for item in restored.replacement_selections)
    try:
        cards = _validate_step(host, step)
        if action == "discard":
            card = next((item for item in cards if item.ref == selected), None)
            kind = ZoneTransitionKind.DISCARD
        else:
            library = host.state.players[step.draw.player].zones["library"]
            card = host.state.cards[library[-1]] if library else None
            kind = ZoneTransitionKind.ORDINARY
        if card is None:
            raise DrawError("The previous discard or mill replacement no longer has its card")
        try:
            prepare_zone_change_replacement(
                host, card, "graveyard", transition_kind=kind,
                selections=selections, error_type=error_type,
            )
        except ReplacementChoiceRequired as required:
            if not _same_movement_choice(required, restored):
                raise DrawError("The pending discard or mill replacement inputs changed") from required
        else:
            raise DrawError("The discard or mill replacement no longer needs a choice")
        _complete_attempt(host, step, action, selected, (*selections, selection))
    except (DrawError, ReplacementEffectError) as exc:
        raise error_type(str(exc)) from exc
