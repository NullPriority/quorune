from __future__ import annotations

"""Validation and mutation for private library partitions and arrivals."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from ..library_order_model import LibraryArrivalOrder

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


def commit_ordered_library_arrivals(
    library: list[str], order: LibraryArrivalOrder, committed: Iterable[CardInstance],
) -> None:
    """Place arriving cards and grant knowledge of their private permutation."""
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
        for member in ordered:
            arrivals[member].known_to = [order.chooser]


__all__ = [
    "LibraryPartitionError",
    "OrderedLibraryPartition",
    "commit_ordered_library_arrivals",
    "commit_ordered_library_partition",
    "partition_refs",
]
