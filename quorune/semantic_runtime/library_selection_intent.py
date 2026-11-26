"""Typed intent for a sealed library selection and its entry attachment."""

from __future__ import annotations

from dataclasses import dataclass

from ..graveyard_arrival_order_model import GraveyardArrivalOrders
from ..linked_library_imprint_model import LIBRARY_IMPRINT_DESTINATION
from ..private_opponent_library_cast_model import PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionArrangement
from .zone_move_intent import _freeze_replacement_selections


@dataclass(frozen=True, slots=True)
class LibrarySelectionIntent:
    actor: str
    player: str
    arrangement: LibrarySelectionArrangement
    reason: str
    source_stack_ref: str
    looked_are_public: bool
    selected_are_public: bool
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    entry_aura_target_ref: str | None = None
    require_graveyard_order: bool = False
    graveyard_orders: GraveyardArrivalOrders | None = None

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.player,
                self.reason,
                self.source_stack_ref,
            )
        ):
            raise ValueError(
                "Library selection intents require complete metadata"
            )
        if not isinstance(self.arrangement, LibrarySelectionArrangement):
            raise TypeError(
                "Library selection intents require an immutable arrangement"
            )
        if (
            type(self.looked_are_public) is not bool
            or type(self.selected_are_public) is not bool
        ):
            raise ValueError(
                "Library selection visibility must be a boolean"
            )
        if self.arrangement.selected_destination == LIBRARY_IMPRINT_DESTINATION and (
            self.actor != self.player or self.looked_are_public or self.selected_are_public
        ):
            raise ValueError("Private library imprint is owned by its resolving viewer")
        from ..rules.private_damage_library_cast import damage_library_cast_arrangement

        damage_cast = damage_library_cast_arrangement(self.arrangement)
        hand_partition = (self.actor == self.player and self.arrangement.selected_destination == "hand"
                          and self.arrangement.remainder_destination == "graveyard")
        if (type(self.require_graveyard_order) is not bool or self.require_graveyard_order and not (hand_partition or damage_cast)
                or damage_cast and not self.require_graveyard_order):
            raise ValueError("Library selection requires its own hand and graveyard for arrival order")
        if self.graveyard_orders is not None and (
            not self.require_graveyard_order or not isinstance(self.graveyard_orders, GraveyardArrivalOrders)
            or not self.graveyard_orders.groups or self.graveyard_orders.current_group is not None
        ):
            raise ValueError("Library selection requires complete actual graveyard orders")
        if self.arrangement.selected_destination == PRIVATE_OPPONENT_LIBRARY_CAST_DESTINATION and (
            self.actor == self.player or self.looked_are_public or self.selected_are_public
        ):
            raise ValueError("Private opponent library cast access separates its viewer from its supplier")
        self.arrangement.validate_entry_attachment(self.entry_aura_target_ref, controller_selected=self.actor == self.player,
                                                   looked_are_public=self.looked_are_public)
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Library selection",
            ),
        )
