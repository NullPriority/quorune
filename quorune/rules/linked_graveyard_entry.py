"""Immutable announced-X graveyard choices and the exiles they actually create."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import GameRuleError
from ..linked_exile_return_model import ExiledObjectIdentity
from .graveyard_entry_copy import GraveyardCopyCard, _is_graveyard_creature, graveyard_copy_candidates


LINKED_GRAVEYARD_ENTRY_HANDLER = "replacement.zone.linked-graveyard-exile.v1"
LINKED_GRAVEYARD_ENTRY_CAPABILITY = "zone.entry.announced_x_linked_graveyard_exile"
LINKED_GRAVEYARD_ENTRY_DEPENDENCIES = (
    LINKED_GRAVEYARD_ENTRY_CAPABILITY, "counter.producer.effect_entry",
    "counter.placement.quantity_replacement", "zone.exile.linked_ability_pair",
)
LINKED_GRAVEYARD_ENTRY_OPERATION = "prepare_linked_graveyard_entry"
LINKED_GRAVEYARD_CHOICES_FIELD = "linked_graveyard_entry_choices"
LINKED_GRAVEYARD_RESULTS_FIELD = "linked_graveyard_entry_results"


def _identity_text(value) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


def _distinct_graveyard_cards(cards, controller) -> bool:
    return (
        type(cards) is tuple
        and all(isinstance(card, GraveyardCopyCard) and card.owner == controller for card in cards)
        and all(len({getattr(card, field) for card in cards}) == len(cards)
                for field in ("object_id", "incarnation", "ref"))
    )


@dataclass(frozen=True, slots=True)
class LinkedGraveyardEntrySelection:
    component_id: str
    controller: str
    maximum: int
    cards: tuple[GraveyardCopyCard, ...]

    def __post_init__(self):
        if (not _identity_text(self.component_id) or not _identity_text(self.controller)
                or type(self.maximum) is not int or not _distinct_graveyard_cards(self.cards, self.controller)
                or not 1 <= len(self.cards) <= self.maximum):
            raise ValueError("Linked entry exiles require a distinct subset of the controller's graveyard")

    def to_dict(self):
        return {"component_id": self.component_id, "controller": self.controller,
                "maximum": self.maximum, "cards": [card.to_dict() for card in self.cards]}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"component_id", "controller", "maximum", "cards"}
                or not isinstance(value["cards"], (list, tuple))):
            raise ValueError("Linked graveyard entry selection has a closed shape")
        return cls(value["component_id"], value["controller"], value["maximum"],
                   tuple(GraveyardCopyCard.from_dict(card) for card in value["cards"]))


@dataclass(frozen=True, slots=True)
class LinkedGraveyardEntryOperation:
    controller: str
    component_id: str
    maximum: int
    candidates: tuple[GraveyardCopyCard, ...]

    def __post_init__(self):
        if (not _identity_text(self.component_id) or not _identity_text(self.controller)
                or type(self.maximum) is not int or not _distinct_graveyard_cards(self.candidates, self.controller)
                or not 1 <= self.maximum <= len(self.candidates)):
            raise ValueError("Linked graveyard entry requires a bounded public creature subset")

    def eligible_cards(self, event):
        if event.entry_scope is None:
            return ()
        allowed = set(event.entry_scope.eligible_zone_choices(card.object_id for card in self.candidates))
        return tuple(card for card in self.candidates if card.object_id in allowed)

    def select(self, event, refs) -> LinkedGraveyardEntrySelection:
        if not isinstance(refs, (list, tuple)):
            raise ValueError("Choose a subset of the offered graveyard creature cards")
        legal = {card.ref: card for card in self.eligible_cards(event)}
        if any(type(ref) is not str or ref not in legal for ref in refs):
            raise ValueError("Choose only offered graveyard creature cards")
        return LinkedGraveyardEntrySelection(
            self.component_id, self.controller, min(self.maximum, len(legal)),
            tuple(legal[ref] for ref in sorted(refs)),
        )

    def to_dict(self):
        return {
            "op": LINKED_GRAVEYARD_ENTRY_OPERATION, "schema_version": 1, "controller": self.controller,
            "component_id": self.component_id, "maximum": self.maximum,
            "candidates": [card.to_dict() for card in self.candidates],
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {
                "op", "schema_version", "controller", "component_id", "maximum", "candidates"}
                or value["op"] != LINKED_GRAVEYARD_ENTRY_OPERATION or type(value["schema_version"]) is not int
                or value["schema_version"] != 1 or not isinstance(value["candidates"], (list, tuple))):
            raise ValueError("Linked graveyard entry operation has a closed version-one shape")
        return cls(value["controller"], value["component_id"], value["maximum"],
                   tuple(GraveyardCopyCard.from_dict(card) for card in value["candidates"]))


def linked_graveyard_entry_operation(
    host, *, source, controller: str, component_id: str,
) -> LinkedGraveyardEntryOperation | None:
    from ..semantic_runtime.entry_copy import _announced_copy_amount

    if source is None or source.zone != "stack":
        return None
    maximum = _announced_copy_amount(host, source, controller)
    if not maximum:
        return None
    candidates = tuple(card for card in graveyard_copy_candidates(host, reserved=(source.object_id,))
                       if card.owner == controller)
    if not candidates:
        return None
    return LinkedGraveyardEntryOperation(controller, component_id, min(maximum, len(candidates)), candidates)


def current_linked_graveyard_selection(host, selection: LinkedGraveyardEntrySelection) -> None:
    if not isinstance(selection, LinkedGraveyardEntrySelection) or selection.controller not in host.active_seats:
        raise GameRuleError("Linked graveyard entry requires its captured active controller")
    for expected in selection.cards:
        current = host.state.cards.get(expected.object_id)
        if not expected.matches(current) or not _is_graveyard_creature(host, current):
            raise GameRuleError("A chosen graveyard incarnation is no longer eligible")


@dataclass(frozen=True, slots=True)
class PreparedLinkedGraveyardEntry:
    selection: LinkedGraveyardEntrySelection
    exiles: tuple[Any, ...]
    actual_exiled: tuple[ExiledObjectIdentity, ...]
    consumed_selections: int

    @property
    def counters(self) -> int:
        return 3 * len(self.actual_exiled)


def prepare_linked_graveyard_entry(host, selection, *, replacement_selections=()):
    """Preview the same canonical exile moves that entry will commit."""
    from ..semantic_runtime.zone_replacements import capture_zone_change_replacement_snapshot
    from .graveyard_entry_copy import _after_exile_read_view, _prepare_exile_prefix

    current_linked_graveyard_selection(host, selection)
    moves = tuple((card.object_id, "exile") for card in selection.cards)
    snapshot = capture_zone_change_replacement_snapshot(host, moves, error_type=GameRuleError)
    ids = {card.ref: f"{selection.component_id}:exile:{card.ref}" for card in selection.cards}
    plans, consumed = _prepare_exile_prefix(snapshot, ids, replacement_selections)
    exiles = tuple(plans[card.object_id] for card in selection.cards)
    preview = _after_exile_read_view(host, exiles)
    actual = tuple(
        ExiledObjectIdentity(card.object_id, card.ref, card.logical_object_id)
        for card in (preview.state.cards[record.object_id] for record in selection.cards)
        if card.zone == "exile"
    )
    return PreparedLinkedGraveyardEntry(selection, exiles, actual, consumed)


def apply_linked_graveyard_entry_selection(event, payload, scope, operation, objects):
    if (event.kind != "zone.change" or scope is None or event.affected_object is None
            or payload.get("destination") != "battlefield"
            or payload.get("destination_controller") != operation.controller):
        raise ValueError("Linked graveyard choices require the affected battlefield entry")
    selection = operation.select(event, objects)
    existing = tuple(payload.get(LINKED_GRAVEYARD_CHOICES_FIELD, ()))
    if any(row["component_id"] == selection.component_id for row in existing):
        raise ValueError("The linked entry choice is already reserved")
    payload[LINKED_GRAVEYARD_CHOICES_FIELD] = [*existing, selection.to_dict()]
    return scope.reserve_zone_changes(card.object_id for card in selection.cards)


def linked_graveyard_entry_choices(event):
    return tuple(LinkedGraveyardEntrySelection.from_dict(row)
                 for row in event.payload.get(LINKED_GRAVEYARD_CHOICES_FIELD, ()))


def unprepared_linked_graveyard_entry_choices(event):
    resolved = {row["component_id"] for row in event.payload.get(LINKED_GRAVEYARD_RESULTS_FIELD, ())}
    return tuple(choice for choice in linked_graveyard_entry_choices(event) if choice.component_id not in resolved)


def validate_linked_graveyard_resume(host, batch, effects, selection, *, error_type):
    try:
        if isinstance(selection, Mapping) and selection.get("objects"):
            effect = next((effect for effect in effects if effect.effect_id == selection["effect_id"]), None)
            for operation in effect.operations if effect is not None else ():
                if not isinstance(operation, LinkedGraveyardEntryOperation):
                    continue
                offered = {card.ref: card for card in operation.candidates}
                refs = selection["objects"]
                if any(ref not in offered for ref in refs):
                    raise ValueError("A linked graveyard choice was never offered")
                current_linked_graveyard_selection(host, LinkedGraveyardEntrySelection(
                    operation.component_id, operation.controller, operation.maximum,
                    tuple(offered[ref] for ref in refs),
                ))
        for event in batch.events:
            for captured in linked_graveyard_entry_choices(event):
                current_linked_graveyard_selection(host, captured)
    except (ValueError, GameRuleError) as exc:
        raise error_type(str(exc)) from exc


def materialize_linked_graveyard_entry(event, prepared):
    from dataclasses import replace
    from ..replacement.immutable import thaw_value

    selected = prepared.selection
    if (selected not in unprepared_linked_graveyard_entry_choices(event)
            or selected.controller != event.payload.get("destination_controller")):
        raise ValueError("The prepared exiles must be from this entry's accepted graveyard choice")
    payload = thaw_value(event.payload)
    rows = list(payload.get(LINKED_GRAVEYARD_RESULTS_FIELD, ()))
    rows.append({"component_id": selected.component_id,
                 "actual_exiled": [identity.to_dict() for identity in prepared.actual_exiled]})
    payload[LINKED_GRAVEYARD_RESULTS_FIELD] = rows
    return replace(event, payload=payload)


def linked_graveyard_entry_counters(event):
    from ..replacement.operations import CreateAffectedObjectCounter

    if event.payload.get("destination") != "battlefield":
        return ()
    return tuple(
        CreateAffectedObjectCounter("+1/+1", 3 * len(row["actual_exiled"]),
                                    event.payload["destination_controller"], row["component_id"])
        for row in event.payload.get(LINKED_GRAVEYARD_RESULTS_FIELD, ()) if row["actual_exiled"]
    )


def linked_graveyard_entry_actual_exiles(event, exiles):
    """Match the saved committed destinations to the approved entry choice."""
    choices = {choice.component_id: choice for choice in linked_graveyard_entry_choices(event)}
    if not choices:
        return ()
    expected = {plan.object_id for plan in exiles if plan.destination == "exile"}
    identities = []
    seen = set()
    for row in event.payload.get(LINKED_GRAVEYARD_RESULTS_FIELD, ()):
        if (set(row) != {"component_id", "actual_exiled"} or row["component_id"] not in choices
                or row["component_id"] in seen or not isinstance(row["actual_exiled"], (tuple, list))):
            raise ValueError("A linked entry result requires its distinct captured component")
        selected = {card.object_id for card in choices[row["component_id"]].cards}
        actual = tuple(ExiledObjectIdentity.from_dict(identity) for identity in row["actual_exiled"])
        if {card.object_id for card in actual} != expected.intersection(selected) or len(set(actual)) != len(actual):
            raise ValueError("Linked entry results must use the selected actual exiles")
        seen.add(row["component_id"])
        identities.extend(actual)
    if seen != choices.keys():
        raise ValueError("A linked entry is missing its actual exile result")
    return tuple(identities)


LINKED_GRAVEYARD_ENTRY_SOURCE_FIELD = "linked_graveyard_entry_source"


def linked_graveyard_entry_scope(card):
    from ..replacement.face_status_copy import current_copy_history
    from ..replacement.immutable import immutable_fingerprint

    history = current_copy_history(card)
    expiry = history.ordered[-1][0].value if history.ordered else None
    return {"expiry": expiry, "form": immutable_fingerprint(history.effective.to_dict())}


def has_linked_graveyard_entry_source(card) -> bool:
    saved = card.annotations.get(LINKED_GRAVEYARD_ENTRY_SOURCE_FIELD)
    return saved is not None and saved == linked_graveyard_entry_scope(card)
