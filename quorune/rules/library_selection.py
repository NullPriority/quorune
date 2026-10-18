from __future__ import annotations

"""Typed fixed library selection arrangements and authoritative commit."""

from dataclasses import dataclass
import random
from typing import Any, Mapping, Protocol, Sequence

from ..model import CardInstance
from .library_partition import (
    LibraryPartitionError,
    OrderedLibraryPartition,
    commit_ordered_library_partition,
    partition_refs,
)


class LibrarySelectionError(ValueError):
    """A fixed library selection or its current identities are malformed."""


@dataclass(frozen=True, slots=True)
class LibrarySelectionObjectIdentity:
    object_id: str
    logical_object_id: str
    ref: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.object_id, self.logical_object_id, self.ref)
        ):
            raise LibrarySelectionError(
                "Library selection identities must be complete"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "ref": self.ref,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "LibrarySelectionObjectIdentity":
        if not isinstance(value, Mapping) or set(value) != {
            "object_id",
            "logical_object_id",
            "ref",
        }:
            raise LibrarySelectionError(
                "Library selection identity fields are malformed"
            )
        return cls(
            object_id=value["object_id"],
            logical_object_id=value["logical_object_id"],
            ref=value["ref"],
        )


@dataclass(frozen=True, slots=True)
class LibrarySelectionArrangement:
    looked: tuple[LibrarySelectionObjectIdentity, ...]
    selected_refs: tuple[str, ...]
    remainder_refs: tuple[str, ...]
    remainder_destination: str
    remainder_order: str
    selected_destination: str='hand'

    def __post_init__(self) -> None:
        looked = tuple(self.looked)
        if (
            any(
                not isinstance(value, LibrarySelectionObjectIdentity)
                for value in looked
            )
            or len({value.object_id for value in looked}) != len(looked)
            or len({value.logical_object_id for value in looked}) != len(looked)
            or len({value.ref for value in looked}) != len(looked)
            or self.remainder_destination not in {
                "graveyard",
                "library_bottom",
                "exile",
            }
            or self.selected_destination not in {'hand','library_top'}
            or (self.selected_destination=='library_top')!=(self.remainder_destination=='exile')
            or self.remainder_order not in {"chosen", "random"}
        ):
            raise LibrarySelectionError(
                "Library selection arrangement is malformed"
            )
        try:
            selected = partition_refs(
                self.selected_refs,
                field="selected_refs",
            )
            remainder = partition_refs(
                self.remainder_refs,
                field="remainder_refs",
            )
        except LibraryPartitionError as exc:
            raise LibrarySelectionError(str(exc)) from exc
        if not looked:
            if selected or remainder:
                raise LibrarySelectionError(
                    "An empty library selection cannot contain cards"
                )
        else:
            try:
                partition = OrderedLibraryPartition(
                    looked_top_first=tuple(value.ref for value in looked),
                    top_top_first=selected,
                    destination_refs=remainder,
                    destination=self.remainder_destination,
                )
            except LibraryPartitionError as exc:
                raise LibrarySelectionError(str(exc)) from exc
            selected = partition.top_top_first
            remainder = partition.destination_refs
        object.__setattr__(self, "looked", looked)
        object.__setattr__(self, "selected_refs", selected)
        object.__setattr__(self, "remainder_refs", remainder)


class LibrarySelectionCommitHost(Protocol):
    state: Any

    def _require_seat(self, seat: str, *, in_game: bool = False) -> None: ...

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str],
        owned_only: bool = False,
    ) -> CardInstance: ...

    def _log(self, *args: Any, **kwargs: Any) -> None: ...


def _validated_objects(
    host: LibrarySelectionCommitHost,
    *,
    actor: str,
    player: str,
    arrangement: LibrarySelectionArrangement,
) -> dict[str, CardInstance]:
    host._require_seat(actor, in_game=True)
    host._require_seat(player, in_game=True)
    cards: dict[str, CardInstance] = {}
    for identity in arrangement.looked:
        card = host._resolve_object(
            actor,
            identity.ref,
            zones={"library"},
            owned_only=(actor == player),
        )
        if (
            card.owner != player
            or card.object_id != identity.object_id
            or card.logical_object_id != identity.logical_object_id
        ):
            raise LibrarySelectionError(
                "A looked-at library card changed identity before selection"
            )
        cards[identity.ref] = card
    if arrangement.looked:
        library = host.state.players[player].zones["library"]
        looked_ids = tuple(value.object_id for value in arrangement.looked)
        current_top = tuple(reversed(library[-len(looked_ids) :]))
        if current_top != looked_ids:
            raise LibrarySelectionError(
                "The looked-at library top changed before selection"
            )
    return cards


def commit_library_selection(
    host: LibrarySelectionCommitHost,
    *,
    actor: str,
    player: str,
    arrangement: LibrarySelectionArrangement,
    reason: str,
    source_stack_ref: str,
    looked_are_public: bool,
    selected_are_public: bool,
    replacement_selections: Sequence[
        str | None | Mapping[str, Any]
    ] = (),
) -> tuple[str, ...]:
    """Commit one identity-pinned selection and remainder as one process."""

    from ..zone_transitions import ZoneTransitionOwner

    if (
        type(reason) is not str
        or not reason
        or type(source_stack_ref) is not str
        or not source_stack_ref
        or type(looked_are_public) is not bool
        or type(selected_are_public) is not bool
    ):
        raise LibrarySelectionError(
            "Library selection commit metadata is malformed"
        )
    cards = _validated_objects(
        host,
        actor=actor,
        player=player,
        arrangement=arrangement,
    )
    if arrangement.selected_destination=='library_top':
        return _commit_top_exile_selection(host,actor=actor,player=player,arrangement=arrangement,cards=cards,
            reason=reason,replacement_selections=replacement_selections)
    changes = [
        (cards[ref].object_id, "hand")
        for ref in arrangement.selected_refs
    ]
    if arrangement.remainder_destination == "graveyard":
        changes.extend(
            (cards[ref].object_id, "graveyard")
            for ref in reversed(arrangement.remainder_refs)
        )
    if changes:
        ZoneTransitionOwner(host).move_cards_simultaneously(
            changes,
            reason=reason,
            log=False,
            replacement_selections=replacement_selections,
        )

    if arrangement.remainder_destination == "library_bottom":
        bottom_ids = [
            cards[ref].object_id for ref in arrangement.remainder_refs
        ]
        if arrangement.remainder_order == "random":
            random.Random(
                f"{host.state.config.seed}|{player}|fixed-library-selection|"
                f"{host.state.event_sequence}|{source_stack_ref}"
            ).shuffle(bottom_ids)
        library = host.state.players[player].zones["library"]
        try:
            commit_ordered_library_partition(
                library,
                top_top_first=(),
                bottom_bottom_first=bottom_ids,
            )
        except LibraryPartitionError as exc:
            raise LibrarySelectionError(str(exc)) from exc
    public_objects = (
        [value.ref for value in arrangement.looked]
        if looked_are_public
        else list(arrangement.selected_refs)
        if selected_are_public
        else []
    )
    changed = [value.object_id for value in arrangement.looked]
    host._log(
        actor,
        "library.selection",
        (
            f"{player} put {len(arrangement.selected_refs)} looked-at "
            "card(s) into hand."
        ),
        {
            "player": player,
            "looked_count": len(arrangement.looked),
            "selected_count": len(arrangement.selected_refs),
            "remainder_count": len(arrangement.remainder_refs),
            "remainder_destination": arrangement.remainder_destination,
            "objects": public_objects,
            "reason": reason,
        },
        visibility=(
            None
            if looked_are_public or selected_are_public
            else [player, "analyst"]
        ),
        importance=1,
        changed_objects=changed,
        changed_players=[player],
    )
    return tuple(value.ref for value in arrangement.looked)


def _commit_top_exile_selection(host,*,actor,player,arrangement,cards,reason,replacement_selections):
    """Keep the selected current objects on top and exile the exact remainder."""
    from ..zone_transitions import ZoneTransitionOwner
    changes=[(cards[ref].object_id,'exile') for ref in arrangement.remainder_refs]
    if changes:ZoneTransitionOwner(host).move_cards_simultaneously(changes,reason=reason,log=False,
        replacement_selections=replacement_selections)
    top=[cards[ref].object_id for ref in arrangement.selected_refs]
    library=host.state.players[player].zones['library']
    if any(oid not in library for oid in top):raise LibrarySelectionError('A selected top card left the library')
    try:commit_ordered_library_partition(library,top_top_first=top)
    except LibraryPartitionError as exc:raise LibrarySelectionError(str(exc)) from exc
    host._log(actor,'library.selection',f'{player} kept {len(top)} looked-at card(s) on top and exiled the remainder.',
        {'player':player,'looked_count':len(arrangement.looked),'selected_count':len(top),
            'remainder_count':len(changes),'selected_destination':'library_top','remainder_destination':'exile','reason':reason},
        visibility=[player,'analyst'],importance=1,changed_objects=[v.object_id for v in arrangement.looked],changed_players=[player])
    return tuple(v.ref for v in arrangement.looked)


__all__ = [
    "LibrarySelectionArrangement",
    "LibrarySelectionCommitHost",
    "LibrarySelectionError",
    "LibrarySelectionObjectIdentity",
    "commit_library_selection",
]
