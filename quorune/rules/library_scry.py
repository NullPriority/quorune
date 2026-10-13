from __future__ import annotations

"""Typed immutable Scry arrangements and their authoritative mutation owner."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .library_partition import (
    LibraryPartitionError,
    OrderedLibraryPartition,
    commit_ordered_library_partition,
    partition_refs,
)


class ScryError(ValueError):
    """A Scry instruction, arrangement, or current library is malformed."""


_LIBRARY_ZONE = "library"


def _refs(value: Any, *, field: str) -> tuple[str, ...]:
    try:
        return partition_refs(value, field=field)
    except LibraryPartitionError as exc:
        raise ScryError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class ScryArrangement:
    """One exact partition and ordering of the cards looked at for Scry."""

    looked_top_first: tuple[str, ...]
    top_top_first: tuple[str, ...]
    bottom_bottom_first: tuple[str, ...]
    legacy_subset_response: bool = False

    def __post_init__(self) -> None:
        looked = _refs(self.looked_top_first, field="looked_top_first")
        if not looked:
            if self.top_top_first or self.bottom_bottom_first or self.legacy_subset_response:
                raise ScryError("Empty Scry cannot contain partitions or legacy subset ordering")
            return
        top = _refs(self.top_top_first, field="top_top_first")
        bottom = _refs(
            self.bottom_bottom_first,
            field="bottom_bottom_first",
        )
        try:
            partition = OrderedLibraryPartition(
                looked_top_first=looked,
                top_top_first=top,
                destination_refs=bottom,
                destination="bottom",
            )
        except LibraryPartitionError as exc:
            raise ScryError(str(exc)) from exc
        if type(self.legacy_subset_response) is not bool:
            raise ScryError("Scry legacy-response state must be boolean")
        object.__setattr__(self, "looked_top_first", partition.looked_top_first)
        object.__setattr__(self, "top_top_first", partition.top_top_first)
        object.__setattr__(self, "bottom_bottom_first", partition.destination_refs)

    @classmethod
    def from_response(
        cls,
        looked_top_first: tuple[str, ...],
        response: Mapping[str, Any],
    ) -> "ScryArrangement":
        cards = response.get("cards")
        if isinstance(cards, (list, tuple)):
            bottom = _refs(cards, field="cards")
            if any(ref not in looked_top_first for ref in bottom):
                raise ScryError(
                    "Legacy Scry bottom choices must be looked-at cards"
                )
            selected = set(bottom)
            return cls(
                looked_top_first=looked_top_first,
                top_top_first=tuple(
                    ref for ref in looked_top_first if ref not in selected
                ),
                bottom_bottom_first=bottom,
                legacy_subset_response=True,
            )
        if not isinstance(cards, Mapping) or set(cards) != {"top", "bottom"}:
            raise ScryError(
                "Scry cards must contain exactly top and bottom orderings"
            )
        return cls(
            looked_top_first=looked_top_first,
            top_top_first=_refs(cards["top"], field="cards.top"),
            bottom_bottom_first=_refs(
                cards["bottom"],
                field="cards.bottom",
            ),
        )


class ScryCommitHost(Protocol):
    def _dispatch_semantic_event(
        self, event: str, context: Mapping[str, Any], **kwargs: Any
    ) -> Any: ...
    state: Any

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str],
        owned_only: bool = False,
    ) -> Any: ...

    def _log(
        self,
        actor: str,
        code: str,
        message: str,
        details: Mapping[str, Any],
        **kwargs: Any,
    ) -> Any: ...


def commit_scry_arrangement(
    host: ScryCommitHost,
    *,
    actor: str,
    player: str,
    arrangement: ScryArrangement,
    reason: str,
    requested_count: int | None = None,
) -> tuple[str, ...]:
    """Atomically apply one previously prepared Scry arrangement."""

    library = host.state.players[player].zones[_LIBRARY_ZONE]
    count = len(arrangement.looked_top_first) if requested_count is None else requested_count
    if type(count) is not int or count <= 0 or count < len(arrangement.looked_top_first):
        raise ScryError("Scry completion requires its positive requested count")
    if not arrangement.looked_top_first:
        if library:
            raise ScryError("Empty Scry arrangement requires an empty library")
        host._log(
            actor, "library.scry", f"{actor} completed Scry without looking at cards.",
            {"count": 0, "requested_count": count, "bottom_count": 0},
            visibility=[actor, "analyst"], importance=1, changed_objects=[],
        )
        host._dispatch_semantic_event(
            "player.scried", {"player": player, "count": count, "looked_count": 0}
        )
        return ()
    looked_ids = tuple(
        host._resolve_object(
            actor,
            ref,
            zones={_LIBRARY_ZONE},
            owned_only=(actor == player),
        ).object_id
        for ref in arrangement.looked_top_first
    )
    current_top = tuple(reversed(library[-len(looked_ids) :]))
    if current_top != looked_ids:
        raise ScryError("The looked-at library top changed before Scry completed")
    by_ref = dict(zip(arrangement.looked_top_first, looked_ids, strict=True))
    bottom_ids = [by_ref[ref] for ref in arrangement.bottom_bottom_first]
    top_ids = [by_ref[ref] for ref in arrangement.top_top_first]
    try:
        commit_ordered_library_partition(
            library,
            top_top_first=top_ids,
            bottom_bottom_first=bottom_ids,
        )
    except LibraryPartitionError as exc:
        raise ScryError(str(exc)) from exc
    changed_ids = (
        bottom_ids
        if arrangement.legacy_subset_response
        else [*bottom_ids, *top_ids]
    )
    host._log(
        actor,
        "library.scry",
        f"{actor} put {len(bottom_ids)} card(s) on the bottom.",
        {
            "count": len(looked_ids),
            "bottom_count": len(bottom_ids),
        },
        visibility=[actor, "analyst"],
        importance=1,
        changed_objects=changed_ids,
    )
    host._dispatch_semantic_event(
        "player.scried", {"player": player, "count": count, "looked_count": len(looked_ids)}
    )
    return arrangement.looked_top_first


__all__ = [
    "ScryArrangement",
    "ScryCommitHost",
    "ScryError",
    "commit_scry_arrangement",
]
