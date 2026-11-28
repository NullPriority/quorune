from __future__ import annotations

"""Resolve a replaced draw through its public prefix and ordered remainder."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..object_predicate import ObjectQuerySpec
from ..replacement import FrozenMap, ReplacementEffectError
from ..replacement.immutable import thaw_value
from ..replacement.ordering import ReplacementChoiceRequired, replacement_choice_payload
from ..reveal_until_library_selection_model import REVEAL_UNTIL_OPERATION, read_reveal_until_prefix
from ..rules.library_partition import commit_ordered_library_arrivals, partition_refs
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.revealed_library_bottom import RevealedLibraryBottomOrder
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from ..semantic_runtime.zone_replacements import prepare_zone_change_replacement
from ..zone_trigger_events import ZoneTransitionKind
from .continuation import DrawResume
from .discard_coordination import _same_movement_choice
from .model import DRAW_LIBRARY_CHOICE_EVENT, DrawError, DrawLibraryChoiceOccurrence


CATEGORY_DECISION = "draw.library_kind"
BOTTOM_DECISION = "draw.library_bottom"
MOVEMENT_RESUME = "draw_library_choice_move"
_FIELD = "draw_library_choice"
_CATEGORIES = ("land", "nonland")
_REASON = "library draw replacement"


@dataclass(frozen=True, slots=True)
class LibraryChoiceDrawContinuation:
    occurrence: DrawLibraryChoiceOccurrence
    after: DrawResume
    turn_sequence: int
    category: str | None = None
    revealed: tuple[LibrarySelectionObjectIdentity, ...] = ()
    matched: bool = False
    reveal_event_id: int | None = None

    def __post_init__(self):
        if not isinstance(self.occurrence, DrawLibraryChoiceOccurrence) or not isinstance(self.after, DrawResume):
            raise DrawError("The library choice requires its replaced draw and remaining instructions")
        if type(self.turn_sequence) is not int or self.turn_sequence < 0 or type(self.matched) is not bool:
            raise DrawError("The library choice has malformed turn or match facts")
        if self.category is None:
            if self.revealed or self.matched or self.reveal_event_id is not None:
                raise DrawError("The unanswered category choice cannot carry a revealed prefix")
            return
        if (
            self.category not in _CATEGORIES or not self.revealed
            or type(self.reveal_event_id) is not int or self.reveal_event_id <= self.occurrence.journal_event_id
            or any(not isinstance(card, LibrarySelectionObjectIdentity) for card in self.revealed)
            or any(len({getattr(card, key) for card in self.revealed}) != len(self.revealed)
                   for key in ("object_id", "logical_object_id", "ref"))
            or len(self.revealed) > self.occurrence.library_size
            or not self.matched and len(self.revealed) != self.occurrence.library_size
        ):
            raise DrawError("The library choice requires its exact revealed prefix and category")

    @property
    def remaining(self):
        return self.revealed[:-1] if self.matched else self.revealed

    def to_dict(self):
        return {
            "schema_version": 1, "occurrence": self.occurrence.to_dict(), "after": self.after.to_dict(),
            "turn_sequence": self.turn_sequence, "category": self.category, "matched": self.matched,
            "revealed": [card.to_dict() for card in self.revealed], "reveal_event_id": self.reveal_event_id,
        }

    @classmethod
    def from_dict(cls, value):
        if (
            not isinstance(value, Mapping) or set(value) != {
                "schema_version", "occurrence", "after", "turn_sequence",
                "category", "matched", "revealed", "reveal_event_id",
            }
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["revealed"], (tuple, list))
        ):
            raise DrawError("Malformed library-choice draw continuation")
        return cls(
            DrawLibraryChoiceOccurrence.from_dict(value["occurrence"]), DrawResume.from_dict(value["after"]),
            value["turn_sequence"], value["category"],
            tuple(LibrarySelectionObjectIdentity.from_dict(card) for card in value["revealed"]),
            value["matched"], value["reveal_event_id"],
        )


def _event(host, identity):
    return next((row for row in reversed(host.state.events) if row.event_id == identity), None)


def _validate_step(host, step):
    occurrence = step.occurrence
    replaced = _event(host, occurrence.journal_event_id)
    expected = {"draw_event_id": occurrence.draw_event_id, "effect_id": occurrence.effect_id,
                "library_size": occurrence.library_size}
    if (
        step.turn_sequence != host.state.turn_sequence or occurrence.seat not in host.active_seats
        or replaced is None or replaced.actor != occurrence.seat or replaced.code != DRAW_LIBRARY_CHOICE_EVENT
        or dict(replaced.details) != expected
    ):
        raise DrawError("The library choice lost its committed original draw")
    if step.category is None:
        if len(host.state.players[occurrence.seat].zones["library"]) != occurrence.library_size:
            raise DrawError("The library changed before the category was chosen")
        return
    reveal = _event(host, step.reveal_event_id)
    if (
        reveal is None or reveal.code != "library.reveal" or reveal.actor != occurrence.seat
        or reveal.details.get("player") != occurrence.seat
        or tuple(card["ref"] for card in reveal.details.get("cards", ())) != tuple(card.ref for card in step.revealed)
    ):
        raise DrawError("The category choice lost its exact public library reveal")


def begin_library_choice_draw(host, occurrence, after):
    step = LibraryChoiceDrawContinuation(occurrence, after, host.state.turn_sequence)
    _validate_step(host, step)
    seat = occurrence.seat
    host.permissions.issue(
        kind=CATEGORY_DECISION, role="pilot", actors=[seat], allowed_actions=["choose"],
        payload_by_actor={seat: {
            "question": "Choose land or nonland for the replaced draw.",
            "options": [{"id": category, "label": category.capitalize()} for category in _CATEGORIES],
            "legal_actions": [{"id": "choose", "action": "choose",
                               "choice_schema": {"field": "category", "legal_values": list(_CATEGORIES)}}],
        }},
        continuation=step.to_dict(),
    )


def _prefix(host, seat, category):
    query = host._semantic_choice_query(seat, effect={"op": REVEAL_UNTIL_OPERATION, "player": seat})
    predicate = (
        ObjectQuerySpec(zones=("library",), types_any=("land",)) if category == "land"
        else ObjectQuerySpec(zones=("library",), excluded_types=("land",))
    )
    rows, matches = read_reveal_until_prefix(query, player=seat, predicates=(predicate,), stop_count=1)
    return tuple(LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref) for row in rows), bool(matches)


def _live_card(host, seat, identity):
    card = host.state.cards.get(identity.object_id)
    if (
        card is None or card.zone != "library" or card.owner != seat or card.ref != identity.ref
        or card.logical_object_id != identity.logical_object_id
        or card.object_id not in host.state.players[seat].zones["library"]
    ):
        raise DrawError("A revealed card changed its library incarnation")
    return card


def _validate_hand_move(host, step):
    _validate_step(host, step)
    seat = step.occurrence.seat
    if not step.matched or _prefix(host, seat, step.category) != (step.revealed, True):
        raise DrawError("The matched card changed before its hand move")
    return _live_card(host, seat, step.revealed[-1])


def _finish_order(host, step, order):
    from .coordinator import resume_after_draw

    _validate_step(host, step)
    seat = step.occurrence.seat
    cards = {identity.ref: _live_card(host, seat, identity) for identity in step.remaining}
    ordered = partition_refs(order, field="revealed library bottom order")
    if set(ordered) != set(cards):
        raise DrawError("Choose the order of exactly the revealed remainder")
    if ordered:
        arrival = RevealedLibraryBottomOrder(seat, "bottom", tuple(cards[ref].object_id for ref in ordered))
        commit_ordered_library_arrivals(host.state.players[seat].zones["library"], arrival, tuple(cards.values()))
        host._log(
            seat, "library.revealed_remainder_bottom",
            f"{seat} ordered the revealed cards on the bottom of their library.",
            {"count": len(cards), "objects": list(cards), "draw_event_id": step.occurrence.draw_event_id},
            changed_objects=[card.object_id for card in cards.values()], changed_players=[seat], importance=1,
        )
    resume_after_draw(host, step.after)


def _begin_order(host, step):
    if len(step.remaining) < 2:
        _finish_order(host, step, tuple(identity.ref for identity in step.remaining))
        return
    seat = step.occurrence.seat
    options = [{"id": identity.ref, "name": _live_card(host, seat, identity).printed_name}
               for identity in step.remaining]
    host.permissions.issue(
        kind=BOTTOM_DECISION, role="pilot", actors=[seat], allowed_actions=["order"],
        payload_by_actor={seat: {
            "question": "Order the revealed remainder from bottom to top.",
            "options": options,
            "legal_actions": [{"id": "order", "action": "order",
                               "choice_schema": {"order": [row["id"] for row in options]}}],
        }},
        continuation=step.to_dict(),
    )


def _attempt_hand_move(host, step, selections=()):
    seat = step.occurrence.seat
    card = _validate_hand_move(host, step)
    try:
        host.move_card(
            card.object_id, "hand", reason=_REASON, semantic_events=True,
            reveal_to=tuple(host.active_seats), transition_kind=ZoneTransitionKind.ORDINARY,
            replacement_selections=selections,
        )
    except ReplacementChoiceRequired as required:
        chooser = required.pending.choice.chooser
        host.permissions.issue(
            kind="replacement.order", role="pilot", actors=[chooser], allowed_actions=["choose"],
            payload_by_actor={chooser: replacement_choice_payload(required.pending, required.effects)},
            continuation={
                "replacement_resume_kind": MOVEMENT_RESUME, _FIELD: step.to_dict(),
                "replacement_selections": list(selections), "replacement_batch": required.batch.to_dict(),
                "replacement_effects": [effect.to_dict() for effect in required.effects],
            },
        )
        return
    _begin_order(host, step)


def _complete_category(host, step, category):
    from .coordinator import resume_after_draw

    _validate_step(host, step)
    seat = step.occurrence.seat
    revealed, matched = _prefix(host, seat, category)
    if not revealed:
        resume_after_draw(host, step.after)
        return
    host.reveal_library_cards_intent(RevealLibraryCardsIntent(
        actor=seat, player=seat, viewer=seat, refs_top_first=tuple(card.ref for card in revealed),
        reason=_REASON, public=True, announce_public_prefix=True,
    ))
    step = replace(step, category=category, revealed=revealed, matched=matched, reveal_event_id=host.state.event_sequence)
    if matched:
        _attempt_hand_move(host, step)
    else:
        _begin_order(host, step)


def complete_library_choice_draw(host, decision):
    step = LibraryChoiceDrawContinuation.from_dict(decision.continuation)
    seat = step.occurrence.seat
    if decision.actors != [seat]:
        raise DrawError("The library choice has a different authenticated drawer")
    response = dict(decision.responses[seat])
    supplied_schema = response.pop("choice_schema", None)
    offered_schema = decision.payload_by_actor[seat]["legal_actions"][0].get("choice_schema")
    if supplied_schema is not None and supplied_schema != offered_schema:
        raise DrawError("The library choice response changed its issued action schema")
    if decision.kind == CATEGORY_DECISION:
        if (step.category is not None or set(response) != {"action", "category"}
                or response["action"] != "choose" or response["category"] not in _CATEGORIES):
            raise DrawError("Choose exactly land or nonland for this replaced draw")
        _complete_category(host, step, response["category"])
        return
    if (decision.kind != BOTTOM_DECISION or step.category is None or len(step.remaining) < 2
            or set(response) != {"action", "order"} or response["action"] != "order"):
        raise DrawError("The revealed remainder requires its original bottom-order choice")
    _finish_order(host, step, response["order"])


def decode_library_choice_movement(continuation_type, value, batch, effects):
    from ..replacement.replay import _decode_combat_selections

    try:
        step = LibraryChoiceDrawContinuation.from_dict(value[_FIELD])
        if not step.matched or len(batch.events) != 1:
            raise DrawError("One revealed match must own the pending hand move")
        event, = batch.events
        if (
            event.kind != "zone.change" or event.affected_object is None
            or event.affected_object.object_id != step.revealed[-1].object_id
            or event.payload.get("origin") != "library" or event.payload.get("destination") != "hand"
        ):
            raise DrawError("The pending movement belongs to another library card or destination")
    except (TypeError, ValueError, KeyError) as exc:
        raise ReplacementEffectError(str(exc)) from exc
    return continuation_type(
        batch=batch, effects=effects, resume_kind=MOVEMENT_RESUME,
        effect=FrozenMap({_FIELD: step.to_dict()}), replacement_selections=_decode_combat_selections(value),
    )


def resume_library_choice_movement(host, restored, selection, *, error_type):
    step = LibraryChoiceDrawContinuation.from_dict(restored.thaw_effect()[_FIELD])
    selections = tuple(thaw_value(item) for item in restored.replacement_selections)
    try:
        card = _validate_hand_move(host, step)
        try:
            prepare_zone_change_replacement(
                host, card, "hand", transition_kind=ZoneTransitionKind.ORDINARY,
                selections=selections, error_type=error_type,
            )
        except ReplacementChoiceRequired as required:
            if not _same_movement_choice(required, restored):
                raise DrawError("The revealed hand-move replacement inputs changed") from required
        else:
            raise DrawError("The revealed hand move no longer needs a replacement choice")
        _attempt_hand_move(host, step, (*selections, selection))
    except (DrawError, ReplacementEffectError) as exc:
        raise error_type(str(exc)) from exc
