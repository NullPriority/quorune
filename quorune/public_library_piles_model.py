from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .library_search_model import (
    DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION, PUBLIC_SEARCH_CHOOSER, PUBLIC_SEARCH_SPLIT_VERSION,
    is_public_search_split,
)
from .replacement.immutable import thaw_value
from typing import Literal

from .rules.library_partition import OrderedLibraryPartition, partition_refs
from .rules.library_selection import LibrarySelectionObjectIdentity


PILE_OPERATION = "public_library_piles"
PILE_MECHANIC = "public-library-piles"
PILE_CAPABILITY = "library.select.public_piles"
PILE_SILVER_CAPABILITY = "library.select.public_pair_marked_exile"
PILE_SILVER_MECHANIC = "public-library-silver-pair"
PILE_SILVER_VERSION = 2
SEARCHED_PAIR_PILE_VERSION = 76
PILE_RULES = ("404.3", "608.2c", "608.2d", "700.3", "701.20a")
PILE_FIELDS = frozenset({"op", "player", "schema_version", "look_count", "opponent_role"})
PILE_NAMES = ("first", "second")
PILE_STAGES = ("choose_opponent", "divide", "choose_pile", "order_graveyard", "commit")
DISTINCT_PUBLIC_SEARCH_SPLIT_CAPABILITY = "library.search.distinct_up_to_four_opponent_split"
PUBLIC_SEARCH_SPLIT_CAPTURE = "public_search_split_capture"
SEARCHED_PILE_VERSION = 2
SEARCHED_PILE_ROLE = "searched_single_card"
DISTINCT_SEARCHED_PILE_VERSION = 3
DISTINCT_SEARCHED_PILE_ROLE = "searched_two_graveyard_cards"
SEARCHED_PILE_VERSIONS = (SEARCHED_PILE_VERSION, DISTINCT_SEARCHED_PILE_VERSION)
_SEARCHED_PILE_STAGES = ("choose_opponent", "choose_card", "commit", "order_graveyard", "shuffle")


def public_search_split_effect(chooser="$target.0"):
    return {
        "op": "search", "schema_version": PUBLIC_SEARCH_SPLIT_VERSION, "zone": "library",
        "selector": {}, "count": {"minimum": 3, "maximum": 3}, "destination": "hand",
        "reveal": True, "shuffle_after": True, PUBLIC_SEARCH_CHOOSER: chooser,
    }


def distinct_public_search_split_effect(chooser="$target.0"):
    return {
        **public_search_split_effect(chooser),
        "schema_version": DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION,
        "count": {"minimum": 0, "maximum": 4},
    }


def validate_public_search_split(effect, *, resolved=False):
    chooser = effect.get(PUBLIC_SEARCH_CHOOSER)
    version = effect.get("schema_version")
    distinct = version == DISTINCT_PUBLIC_SEARCH_SPLIT_VERSION
    if (not isinstance(chooser, str) or not chooser
            or (chooser.startswith("$") if resolved else chooser != "$target.0")
            or type(version) is not int
            or effect.get("reveal") is not True or effect.get("shuffle_after") is not True
            or thaw_value(effect) != (
                distinct_public_search_split_effect(chooser) if distinct else public_search_split_effect(chooser)
            )):
        raise ValueError("Public search split requires one fixed unqualified search and its opponent")
    if any(type(value) is not int for value in effect["count"].values()):
        if distinct:
            raise ValueError("Distinct public search requires its exact private zero-to-four choice")
        raise ValueError("Public search split requires its exact integer count")
    return chooser


def public_search_split_target_schema():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}


@dataclass(frozen=True, slots=True)
class PublicLibraryPileSpec:
    player: str
    look_count: int
    opponent_role: Literal["divider", "chooser"]
    silver_split: bool = False
    searched_pair: bool = False

    @classmethod
    def from_effect(cls, effect: Mapping, *, resolved: bool = False) -> PublicLibraryPileSpec:
        if set(effect) != PILE_FIELDS or effect["op"] != PILE_OPERATION:
            raise ValueError("Public library piles require the complete bounded descriptor")
        version = effect["schema_version"]
        silver_split = version == PILE_SILVER_VERSION
        searched_pair = resolved and version == SEARCHED_PAIR_PILE_VERSION
        if (type(version) is not int or version not in (1, PILE_SILVER_VERSION, SEARCHED_PAIR_PILE_VERSION)
                or type(effect["look_count"]) is not int
                or effect["look_count"] != (2 if silver_split or searched_pair else 5)
                or effect["opponent_role"] not in (("chooser",) if silver_split or searched_pair else ("divider", "chooser"))
                or version == SEARCHED_PAIR_PILE_VERSION and not searched_pair):
            raise ValueError("Unsupported public pile descriptor")
        player = effect["player"]
        if type(player) is not str or not player or (player.startswith("$") if resolved else player != "$controller"):
            raise ValueError("Public library piles have no unbound or arbitrary player")
        return cls(player, effect["look_count"], effect["opponent_role"], silver_split, searched_pair)


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
class SearchedLibraryPileState:
    controller: str
    stack_ref: str
    label: str
    target: str
    looked: tuple[RevealedPileCard, ...]
    stage: str
    chooser: str | None
    chosen: str | tuple[str, ...] | None = None
    schema_version: int = SEARCHED_PILE_VERSION

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in SEARCHED_PILE_VERSIONS:
            raise ValueError("Unknown searched public partition version")
        maximum = 4 if self.chooses_for_graveyard else 3
        if (any(type(value) is not str or not value for value in
                (self.controller, self.stack_ref, self.label, self.target, self.stage))
                or self.target == self.controller or self.stage not in _SEARCHED_PILE_STAGES
                or not 1 <= len(self.looked) <= maximum
                or any(not isinstance(card, RevealedPileCard) for card in self.looked)
                or len(set(self.refs)) != len(self.looked)
                or len({card.identity.logical_object_id for card in self.looked}) != len(self.looked)
                or len({card.identity.object_id for card in self.looked}) != len(self.looked)
                or (self.stage == "choose_opponent") != (self.chooser is None)
                or self.chooser is not None and (type(self.chooser) is not str or not self.chooser
                                                or self.chooser == self.controller)
                or self.chooses_for_graveyard and len(self.looked) <= 2 and self.stage in {"choose_opponent", "choose_card"}):
            raise ValueError("A searched public partition requires its original cards and choice stage")
        assigned = self.chosen in self.refs
        if self.chooses_for_graveyard:
            assigned = (
                type(self.chosen) is tuple
                and len(self.chosen) == min(2, len(self.looked))
                and all(type(ref) is str and ref in self.refs for ref in self.chosen)
                and len(set(self.chosen)) == len(self.chosen)
            )
        if ((self.stage in {"commit", "order_graveyard", "shuffle"}) != assigned
                or self.chosen is not None and not assigned):
            raise ValueError("A searched public partition requires its original cards and choice stage")

    @property
    def chooses_for_graveyard(self):
        return self.schema_version == DISTINCT_SEARCHED_PILE_VERSION

    @property
    def refs(self):
        return tuple(card.identity.ref for card in self.looked)

    @property
    def actor(self):
        return self.chooser if self.stage == "choose_card" else self.controller

    def hand_refs(self):
        if self.chooses_for_graveyard:
            return tuple(ref for ref in self.refs if ref not in (self.chosen or ()))
        return (self.chosen,)

    def graveyard_refs(self):
        if self.chooses_for_graveyard:
            return self.chosen or ()
        return tuple(ref for ref in self.refs if ref != self.chosen)

    def to_dict(self):
        return {
            "controller": self.controller, "stack_ref": self.stack_ref, "label": self.label,
            "target": self.target, "looked": [card.to_dict() for card in self.looked],
            "stage": self.stage, "chooser": self.chooser,
            "chosen": list(self.chosen) if self.chooses_for_graveyard and self.chosen is not None else self.chosen,
            **({"schema_version": self.schema_version} if self.chooses_for_graveyard else {}),
        }

    @classmethod
    def from_dict(cls, value):
        old = {"controller", "stack_ref", "label", "target", "looked", "stage", "chooser", "chosen"}
        if not isinstance(value, Mapping) or set(value) not in (old, old | {"schema_version"}):
            raise ValueError("Searched public pile state is malformed")
        if not isinstance(value["looked"], (list, tuple)):
            raise ValueError("Searched public pile state is malformed")
        fields = dict(value)
        if "schema_version" in fields:
            if type(fields["schema_version"]) is not int or fields["schema_version"] != DISTINCT_SEARCHED_PILE_VERSION:
                raise ValueError("Invalid distinct searched pile version")
            chosen = fields["chosen"]
            if chosen is not None and not isinstance(chosen, (list, tuple)):
                raise ValueError("Distinct searched public selections require a card array")
            fields["chosen"] = tuple(chosen) if chosen is not None else None
        fields["looked"] = tuple(RevealedPileCard.from_dict(row) for row in value["looked"])
        return cls(**fields)


def searched_pile_effect(state):
    return {
        "op": PILE_OPERATION, "schema_version": state.schema_version, "player": state.actor,
        "look_count": 4 if state.chooses_for_graveyard else 3,
        "opponent_role": DISTINCT_SEARCHED_PILE_ROLE if state.chooses_for_graveyard else SEARCHED_PILE_ROLE,
        "_public_library_piles": state.to_dict(),
    }


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
        if spec.silver_split and (len(self.looked) != 2 or self.groups is None
                or self.groups.top_top_first != self.refs[:1] or self.groups.destination_refs != self.refs[1:]
                or self.stage not in ("choose_opponent", "choose_pile", "commit")):
            raise ValueError("The marked library split must preserve two individual cards")
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
