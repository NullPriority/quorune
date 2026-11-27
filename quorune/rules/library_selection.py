from __future__ import annotations

"""Typed fixed library selection arrangements and authoritative commit."""

from dataclasses import dataclass
import random
from typing import Any, Mapping, Protocol, Sequence

from ..model import CardInstance
from ..linked_library_imprint_model import LIBRARY_IMPRINT_DESTINATION
from ..private_opponent_library_cast_model import PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION
from ..library_battlefield_model import LIBRARY_SHUFFLE_REMAINDER, RevealedCreatureEntryFollowup
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
    entry_tapped: bool=False
    entry_followup: RevealedCreatureEntryFollowup | None = None

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
                LIBRARY_SHUFFLE_REMAINDER,
            }
            or self.selected_destination not in {
                "hand", "library_top", "battlefield", LIBRARY_IMPRINT_DESTINATION, PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION,
            }
            or type(self.entry_tapped) is not bool
            or self.entry_tapped and self.selected_destination!='battlefield'
            or self.remainder_destination == "exile" and self.selected_destination != "library_top"
            or self.selected_destination == "library_top" and not (
                self.remainder_destination == "exile"
                or self.remainder_destination == "library_bottom" and self.remainder_order == "random"
            )
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
        if self.selected_destination == LIBRARY_IMPRINT_DESTINATION and (
                self.remainder_destination != "library_bottom" or self.remainder_order != "chosen"
                or len(looked) > 4 or len(selected) != int(bool(looked))):
            raise LibrarySelectionError("A private library imprint must exile one when possible and order the rest")
        from .private_damage_library_cast import damage_library_cast_arrangement

        if self.selected_destination == PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION and (
                len(selected) != int(bool(looked))
                or not (damage_library_cast_arrangement(self) and len(looked) <= 3
                        or self.remainder_destination == "library_bottom" and self.remainder_order == "random" and len(looked) <= 4)):
            raise LibrarySelectionError("Private opponent library access exiles one and randomly bottoms the remainder")
        if self.selected_destination == "library_top" and self.remainder_destination == "library_bottom" and len(selected) > 1:
            raise LibrarySelectionError("The private top selection keeps at most one card")
        if ((self.remainder_destination == LIBRARY_SHUFFLE_REMAINDER) != (self.entry_followup is not None)
                or self.entry_followup is not None and (
                    not isinstance(self.entry_followup, RevealedCreatureEntryFollowup)
                    or self.selected_destination != "battlefield" or self.remainder_order != "random"
                    or self.entry_tapped or len(selected) > 1)):
            raise LibrarySelectionError("The whole-library shuffle requires its revealed entry followup")
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


    def validate_entry_attachment(
        self, target_ref: str | None, *, controller_selected: bool, looked_are_public: bool = False,
    ) -> None:
        if target_ref is None:
            return
        public_stopping_match = (
            looked_are_public and bool(self.looked)
            and self.selected_refs == (self.looked[-1].ref,)
            and self.remainder_destination == "library_bottom" and self.remainder_order == "random"
        )
        if (
            type(target_ref) is not str
            or not target_ref
            or not controller_selected
            or self.selected_destination != "battlefield"
            or not (len(self.looked) == 1 or public_stopping_match)
            or len(self.selected_refs) != 1
            or self.entry_followup is not None
        ):
            raise LibrarySelectionError("An Aura attachment requires one controller-selected library entrant")


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
    entry_aura_target_ref: str | None = None,
    require_graveyard_order: bool = False,
    graveyard_orders=None,
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
    from .private_damage_library_cast import damage_library_cast_arrangement

    damage_cast = damage_library_cast_arrangement(arrangement)
    hand_partition = actor == player and arrangement.selected_destination == "hand" and arrangement.remainder_destination == "graveyard"
    if (type(require_graveyard_order) is not bool or require_graveyard_order and not (hand_partition or damage_cast)
            or graveyard_orders is not None and not require_graveyard_order
            or damage_cast and not require_graveyard_order):
        raise LibrarySelectionError("Library selection arrival orders require its own hand and graveyard")
    cards = _validated_objects(
        host,
        actor=actor,
        player=player,
        arrangement=arrangement,
    )
    if damage_cast:
        from .private_damage_library_cast import commit_private_damage_library_cast

        return commit_private_damage_library_cast(
            host, actor=actor, player=player, arrangement=arrangement, cards=cards, reason=reason,
            source_stack_ref=source_stack_ref, looked_are_public=looked_are_public,
            selected_are_public=selected_are_public, replacement_selections=replacement_selections,
            graveyard_orders=graveyard_orders,
        )
    if arrangement.selected_destination == PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION:
        from .private_opponent_library_cast import commit_private_opponent_library_cast

        return commit_private_opponent_library_cast(
            host, actor=actor, player=player, arrangement=arrangement, cards=cards, reason=reason,
            source_stack_ref=source_stack_ref, looked_are_public=looked_are_public,
            selected_are_public=selected_are_public, replacement_selections=replacement_selections,
        )
    if arrangement.selected_destination == LIBRARY_IMPRINT_DESTINATION:
        from .library_imprint_selection import commit_library_imprint

        return commit_library_imprint(
            host, actor=actor, player=player, arrangement=arrangement, cards=cards, reason=reason,
            source_stack_ref=source_stack_ref, looked_are_public=looked_are_public,
            selected_are_public=selected_are_public, replacement_selections=replacement_selections,
        )
    if arrangement.selected_destination == "library_top" and arrangement.remainder_destination == "library_bottom":
        return _commit_private_top_bottom_selection(
            host, actor=actor, player=player, arrangement=arrangement, cards=cards,
            reason=reason, source_stack_ref=source_stack_ref,
        )
    if arrangement.selected_destination=='library_top':
        return _commit_top_exile_selection(
            host, actor=actor, player=player, arrangement=arrangement, cards=cards, reason=reason,
            source_stack_ref=source_stack_ref, looked_are_public=looked_are_public,
            selected_are_public=selected_are_public, replacement_selections=replacement_selections,
        )
    if arrangement.selected_destination=='battlefield':
        from .library_battlefield_entry import commit_library_battlefield_selection
        return commit_library_battlefield_selection(host,actor=actor,player=player,arrangement=arrangement,
            cards=cards,reason=reason,source_stack_ref=source_stack_ref,
            looked_are_public=looked_are_public,selected_are_public=selected_are_public,
            replacement_selections=replacement_selections, entry_aura_target_ref=entry_aura_target_ref)
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
            **({"require_graveyard_order": True, "graveyard_orders": graveyard_orders}
               if require_graveyard_order else {}),
        )

    if arrangement.remainder_destination == "library_bottom":
        bottom_ids = [
            cards[ref].object_id for ref in arrangement.remainder_refs
        ]
        if arrangement.remainder_order == "random":
            _shuffle_bottom_selection(host, player, source_stack_ref, bottom_ids)
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


def _shuffle_bottom_selection(host, player, source_stack_ref, bottom_ids):
    random.Random(
        f"{host.state.config.seed}|{player}|fixed-library-selection|"
        f"{host.state.event_sequence}|{source_stack_ref}"
    ).shuffle(bottom_ids)


def _commit_private_top_bottom_selection(host, *, actor, player, arrangement, cards, reason, source_stack_ref):
    top = [cards[ref].object_id for ref in arrangement.selected_refs]
    bottom = [cards[ref].object_id for ref in arrangement.remainder_refs]
    _shuffle_bottom_selection(host, player, source_stack_ref, bottom)
    try:
        commit_ordered_library_partition(
            host.state.players[player].zones["library"], top_top_first=top, bottom_bottom_first=bottom,
        )
    except LibraryPartitionError as exc:
        raise LibrarySelectionError(str(exc)) from exc
    host._log(
        actor, "library.selection",
        f"{player} kept {len(top)} looked-at card(s) on top and put the rest on the bottom in a random order.",
        {
            "player": player, "looked_count": len(arrangement.looked), "selected_count": len(top),
            "remainder_count": len(bottom), "selected_destination": "library_top",
            "remainder_destination": "library_bottom", "reason": reason,
        },
        visibility=[player, "analyst"], importance=1,
        changed_objects=[value.object_id for value in arrangement.looked], changed_players=[player],
    )
    return tuple(value.ref for value in arrangement.looked)


def _commit_top_exile_selection(
    host, *, actor, player, arrangement, cards, reason, source_stack_ref, looked_are_public,
    selected_are_public, replacement_selections,
):
    """Keep the selected current objects on top and exile the exact remainder."""
    from ..zone_transitions import ZoneTransitionOwner

    if actor != player:
        from .opponent_library_shuffle import validate_opponent_shuffle_partition_commit

        try:
            validate_opponent_shuffle_partition_commit(
                host, actor=actor, player=player, arrangement=arrangement, source_stack_ref=source_stack_ref,
                looked_are_public=looked_are_public, selected_are_public=selected_are_public,
            )
        except ValueError as exc:
            raise LibrarySelectionError(str(exc)) from exc
    changes=[(cards[ref].object_id,'exile') for ref in arrangement.remainder_refs]
    if changes:ZoneTransitionOwner(host).move_cards_simultaneously(changes,reason=reason,log=False,
        replacement_selections=replacement_selections)
    top=[cards[ref].object_id for ref in arrangement.selected_refs]
    library=host.state.players[player].zones['library']
    if any(oid not in library for oid in top):raise LibrarySelectionError('A selected top card left the library')
    try:commit_ordered_library_partition(library,top_top_first=top)
    except LibraryPartitionError as exc:raise LibrarySelectionError(str(exc)) from exc
    host._log(actor,'library.selection',f'{actor} kept {len(top)} looked-at card(s) on top and exiled the remainder.',
        {'player':player,'looked_count':len(arrangement.looked),'selected_count':len(top),
            'remainder_count':len(changes),'selected_destination':'library_top','remainder_destination':'exile','reason':reason},
        visibility=[actor,'analyst'],importance=1,changed_objects=[v.object_id for v in arrangement.looked],changed_players=[player])
    return tuple(v.ref for v in arrangement.looked)


__all__ = [
    "LibrarySelectionArrangement",
    "LibrarySelectionCommitHost",
    "LibrarySelectionError",
    "LibrarySelectionObjectIdentity",
    "commit_library_selection",
]
