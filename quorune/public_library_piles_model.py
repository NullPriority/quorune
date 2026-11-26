from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .rules.library_partition import OrderedLibraryPartition, partition_refs
from .rules.library_selection import LibrarySelectionObjectIdentity


PILE_OPERATION = "public_library_piles"
PILE_MECHANIC = "public-library-piles"
PILE_CAPABILITY = "library.select.public_piles"
PILE_RULES = ("404.3", "608.2c", "608.2d", "700.3", "701.20a")
PILE_FIELDS = frozenset({"op", "player", "schema_version", "look_count", "opponent_role"})
PILE_NAMES = ("first", "second")
PILE_STAGES = ("choose_opponent", "divide", "choose_pile", "order_graveyard", "commit")


@dataclass(frozen=True, slots=True)
class PublicLibraryPileSpec:
    player: str
    look_count: int
    opponent_role: Literal["divider", "chooser"]

    @classmethod
    def from_effect(cls, effect: Mapping, *, resolved: bool = False) -> PublicLibraryPileSpec:
        if set(effect) != PILE_FIELDS or effect["op"] != PILE_OPERATION:
            raise ValueError("Public library piles require the complete bounded descriptor")
        if (type(effect["schema_version"]) is not int or effect["schema_version"] != 1
                or type(effect["look_count"]) is not int or effect["look_count"] != 5
                or effect["opponent_role"] not in ("divider", "chooser")):
            raise ValueError("Unsupported public pile descriptor")
        player = effect["player"]
        if type(player) is not str or not player or (player.startswith("$") if resolved else player != "$controller"):
            raise ValueError("Public library piles have no unbound or arbitrary player")
        return cls(player, effect["look_count"], effect["opponent_role"])


@dataclass(frozen=True, slots=True)
class RevealedPileCard:
    identity: LibrarySelectionObjectIdentity
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, LibrarySelectionObjectIdentity) or type(self.name) is not str or not self.name:
            raise ValueError("A revealed pile card requires an exact incarnation and printed name")

    @classmethod
    def from_dict(cls, value: Mapping) -> RevealedPileCard:
        if set(value) != {"identity", "name"}:
            raise ValueError("A revealed pile card has an invalid shape")
        return cls(LibrarySelectionObjectIdentity.from_dict(value["identity"]), value["name"])

    def to_dict(self) -> dict:
        return {"identity": self.identity.to_dict(), "name": self.name}


@dataclass(frozen=True, slots=True)
class PublicLibraryPileState:
    controller: str
    stack_ref: str
    label: str
    looked: tuple[RevealedPileCard, ...]
    stage: str
    opponent: str | None = None
    groups: OrderedLibraryPartition | None = None
    chosen: str | None = None

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(row.identity.ref for row in self.looked)

    def __post_init__(self) -> None:
        if (
            any(type(value) is not str or not value for value in (self.controller, self.stack_ref, self.label))
            or self.stage not in PILE_STAGES
            or not 1 <= len(self.looked) <= 5
            or any(not isinstance(row, RevealedPileCard) for row in self.looked)
            or len(set(self.refs)) != len(self.looked)
            or len({row.identity.object_id for row in self.looked}) != len(self.looked)
            or (self.opponent is not None and (
                type(self.opponent) is not str or not self.opponent or self.opponent == self.controller))
            or (self.groups is not None and (
                not isinstance(self.groups, OrderedLibraryPartition)
                or self.groups.looked_top_first != self.refs or self.groups.destination != "second"))
            or self.chosen not in (None, *PILE_NAMES)
        ):
            raise ValueError("The public library pile continuation is malformed")

    def actor(self, spec: PublicLibraryPileSpec) -> str:
        if self.stage == "choose_opponent":
            if self.opponent is not None or self.chosen is not None or (self.groups is None) != (spec.opponent_role == "divider"):
                raise ValueError("The opponent choice is out of order")
            return self.controller
        if self.stage == "divide":
            if self.groups is not None or self.chosen is not None or (self.opponent is None) != (spec.opponent_role == "chooser"):
                raise ValueError("The pile division is out of order")
            return self.opponent if spec.opponent_role == "divider" else self.controller
        if self.groups is None or self.opponent is None:
            raise ValueError("A pile choice requires a complete split and its selected opponent")
        if self.stage == "choose_pile":
            if self.chosen is not None:
                raise ValueError("The hand pile has already been chosen")
            return self.opponent if spec.opponent_role == "chooser" else self.controller
        if self.chosen is None or (self.stage == "order_graveyard" and len(self.graveyard_refs()) < 2):
            raise ValueError("The resolved piles do not match their graveyard-order phase")
        return self.controller

    def hand_refs(self) -> tuple[str, ...]:
        if self.groups is None or self.chosen not in PILE_NAMES:
            raise ValueError("The hand pile has not been chosen")
        return self.groups.top_top_first if self.chosen == "first" else self.groups.destination_refs

    def graveyard_refs(self) -> tuple[str, ...]:
        hand = set(self.hand_refs())
        return tuple(ref for ref in self.refs if ref not in hand)

    def split(self, submitted: Mapping) -> OrderedLibraryPartition:
        if not isinstance(submitted, Mapping) or set(submitted) != set(PILE_NAMES):
            raise ValueError("Provide exactly the first and second pile")
        first = partition_refs(submitted["first"], field="first")
        second = partition_refs(submitted["second"], field="second")
        partition = OrderedLibraryPartition(self.refs, first, second, "second")
        first_ids, second_ids = set(partition.top_top_first), set(partition.destination_refs)
        return OrderedLibraryPartition(
            self.refs, tuple(ref for ref in self.refs if ref in first_ids),
            tuple(ref for ref in self.refs if ref in second_ids), "second",
        )

    def to_dict(self) -> dict:
        return {
            "controller": self.controller, "stack_ref": self.stack_ref, "label": self.label,
            "looked": [row.to_dict() for row in self.looked], "stage": self.stage, "opponent": self.opponent,
            "groups": None if self.groups is None else {
                "first": self.groups.top_top_first, "second": self.groups.destination_refs,
            }, "chosen": self.chosen,
        }

    @classmethod
    def from_dict(cls, value: Mapping) -> PublicLibraryPileState:
        if set(value) != {"controller", "stack_ref", "label", "looked", "stage", "opponent", "groups", "chosen"}:
            raise ValueError("The public library pile continuation has an invalid shape")
        if not isinstance(value["looked"], (tuple, list)):
            raise ValueError("Revealed cards must be ordered")
        looked = tuple(RevealedPileCard.from_dict(row) for row in value["looked"])
        groups = None
        if value["groups"] is not None:
            encoded = value["groups"]
            if not isinstance(encoded, Mapping) or set(encoded) != set(PILE_NAMES):
                raise ValueError("Saved pile membership is malformed")
            groups = OrderedLibraryPartition(
                tuple(row.identity.ref for row in looked), encoded["first"], encoded["second"], "second",
            )
        return cls(value["controller"], value["stack_ref"], value["label"], looked, value["stage"],
                   value["opponent"], groups, value["chosen"])
