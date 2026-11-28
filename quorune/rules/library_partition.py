from __future__ import annotations

"""Validation and mutation for private library partitions and arrivals."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from ..errors import GameRuleError
from ..library_order_model import DepartedLibraryArrivalOrder, LibraryArrivalOrder, RandomGraveyardBottomOrder

if TYPE_CHECKING:
    from ..model import CardInstance


class LibraryPartitionError(ValueError):
    """A looked-at library partition is malformed."""


def partition_refs(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise LibraryPartitionError(
            f"{field} must be a list of object references"
        )
    result = tuple(value)
    if any(type(ref) is not str or not ref for ref in result):
        raise LibraryPartitionError(
            f"{field} must contain nonempty object references"
        )
    if len(result) != len(set(result)):
        raise LibraryPartitionError(
            f"{field} must not contain duplicate references"
        )
    return result


@dataclass(frozen=True, slots=True)
class OrderedLibraryPartition:
    """One exact looked-at set split into an ordered top and destination."""

    looked_top_first: tuple[str, ...]
    top_top_first: tuple[str, ...]
    destination_refs: tuple[str, ...]
    destination: str

    def __post_init__(self) -> None:
        looked = partition_refs(self.looked_top_first, field="looked_top_first")
        if not looked:
            raise LibraryPartitionError(
                "An ordered library partition requires looked-at cards"
            )
        top = partition_refs(self.top_top_first, field="top_top_first")
        destination_refs = partition_refs(
            self.destination_refs,
            field=f"{self.destination}_refs",
        )
        if type(self.destination) is not str or not self.destination:
            raise LibraryPartitionError(
                "An ordered library partition requires a destination"
            )
        if set(top).intersection(destination_refs):
            raise LibraryPartitionError(
                "Ordered library partition groups must be disjoint"
            )
        if len(top) + len(destination_refs) != len(looked) or set(
            (*top, *destination_refs)
        ) != set(looked):
            raise LibraryPartitionError(
                "Ordered library partition groups must contain every looked-at card"
            )
        object.__setattr__(self, "looked_top_first", looked)
        object.__setattr__(self, "top_top_first", top)
        object.__setattr__(self, "destination_refs", destination_refs)


def commit_ordered_library_partition(
    library: list[str],
    *,
    top_top_first: Sequence[str],
    bottom_bottom_first: Sequence[str] = (),
) -> None:
    """Place exact current library objects at the ordered top and bottom."""

    top = partition_refs(top_top_first, field="top_top_first")
    bottom = partition_refs(
        bottom_bottom_first,
        field="bottom_bottom_first",
    )
    selected = (*top, *bottom)
    if len(selected) != len(set(selected)) or any(
        object_id not in library for object_id in selected
    ):
        raise LibraryPartitionError(
            "Ordered library cards must be unique current library objects"
        )
    selected_ids = set(selected)
    untouched = [
        object_id for object_id in library if object_id not in selected_ids
    ]
    library[:] = [*bottom, *untouched, *reversed(top)]


def validated_library_search_results(state, seat, object_ids, *, public, expected_zone, announce):
    if (type(public) is not bool or expected_zone not in {"library", "hand"}
            or type(announce) is not bool or (announce and not public)):
        raise GameRuleError("Search-result visibility is malformed")
    if any(type(object_id) is not str or object_id not in state.cards for object_id in object_ids):
        raise GameRuleError("Search-result identities are unavailable")
    if len(object_ids) != len(set(object_ids)):
        raise GameRuleError("Search-result identities must be unique")
    cards = [state.cards[object_id] for object_id in object_ids]
    if any(card.owner != seat or card.zone != expected_zone
            or card.object_id not in state.players[seat].zones[expected_zone] for card in cards):
        raise GameRuleError("Search-result visibility requires the expected owned zone")
    return cards


def publish_library_search_reveal(host, seat, cards, *, announce):
    shown = sorted(cards, key=lambda card: card.ref) if announce else cards
    public_details = {"objects": [card.ref for card in shown]}
    summary = f"{seat} revealed search results."
    if announce:
        names = [card.printed_name for card in shown]
        public_details["names"] = names
        summary = f"{seat} revealed {', '.join(names)}."
    host._log(
        seat, "library.search.reveal", summary, public_details, importance=3 if announce else 1,
        changed_objects=[card.object_id for card in shown], changed_players=[seat],
    )


def place_revealed_library_search_results_on_top(host, owner, seat, top_first, *, reason):
    """Reveal the set in public and retain the proposed permutation privately."""
    owner.reveal_library_search_results(seat, top_first, public=True, announce_public_results=True)
    owner.shuffle_library(seat, reason=reason, excluded_object_ids=top_first)
    library = host.state.players[seat].zones["library"]
    commit_ordered_library_partition(library, top_top_first=top_first)
    if len(top_first) > 1:
        for object_id in top_first:
            card = host.state.cards[object_id]
            card.known_to = [seat]
            card.revealed_to = [seat]


def place_private_library_find_on_top(state, seat, object_id):
    """Advance private positional knowledge after shuffling around a found card."""
    card, = validated_library_search_results(
        state, seat, (object_id,), public=False, expected_zone="library", announce=False,
    )
    commit_ordered_library_partition(state.players[seat].zones["library"], top_top_first=(object_id,))
    card.known_to = [seat]
    card.revealed_to = []


def validate_ordered_library_destination(host, cards_by_id, changes, order: LibraryArrivalOrder) -> str:
    if not isinstance(order, LibraryArrivalOrder) or (
        len(changes) != len(order.members) or set(changes) != {(member, "library") for member in order.members}
    ):
        raise GameRuleError("Library order must describe the whole requested batch")
    host._require_seat(order.chooser, in_game=True)
    cards = tuple(cards_by_id.get(member) for member in order.members)
    if any(card is None or card.zone not in order.origin_zones or card.permanent_composition is not None for card in cards):
        raise GameRuleError("Ordered library movements require the original public-zone cards")
    owners = {card.owner for card in cards}
    if len(owners) != 1:
        raise GameRuleError("Ordered library movements require a single library")
    owner, = owners
    if isinstance(order, (DepartedLibraryArrivalOrder, RandomGraveyardBottomOrder)) and order.chooser != owner:
        raise GameRuleError("The library owner must order a departed card group")
    return owner


def commit_ordered_library_arrivals(
    library: list[str], order: LibraryArrivalOrder, committed: Iterable[CardInstance],
) -> None:
    """Place actual arrivals and apply the ordering operation's positional knowledge."""
    arrivals = {card.object_id: card for card in committed if card.zone == "library"}
    ordered = tuple(member for member in order.members if member in arrivals)
    if not ordered:
        return
    if type(order.position) is int:
        selected = set(ordered)
        previous = [object_id for object_id in library if object_id not in selected]
        insertion = max(0, len(previous) - order.position + 1)
        library[:] = [*previous[:insertion], *reversed(ordered), *previous[insertion:]]
    else:
        commit_ordered_library_partition(
            library,
            top_top_first=ordered if order.position == "top" else (),
            bottom_bottom_first=ordered if order.position == "bottom" else (),
        )
    if len(ordered) > 1:
        random_bottom = isinstance(order, RandomGraveyardBottomOrder)
        for member in ordered:
            card = arrivals[member]
            card.known_to = [] if random_bottom else [order.chooser]
            if random_bottom:
                card.revealed_to = []


__all__ = [
    "LibraryPartitionError",
    "OrderedLibraryPartition",
    "commit_ordered_library_arrivals",
    "commit_ordered_library_partition",
    "partition_refs",
    "validate_ordered_library_destination",
]
