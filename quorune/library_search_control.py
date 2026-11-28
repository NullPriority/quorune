"""Authority retained solely for an opponent's current search decision."""

from collections.abc import Mapping
from dataclasses import dataclass


CONTROL_FIELD = "controlled_library_search"
RESULT_CHOICE = "search.exile_replacement"
CONTROL_KINDS = frozenset({"semantic.search", "search.fetch", "semantic.choice", RESULT_CHOICE})


@dataclass(frozen=True, slots=True)
class SearchControlSource:
    ref: str
    identity: str
    controller: str
    timestamp: int

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.ref, self.identity, self.controller))
                or type(self.timestamp) is not int or self.timestamp < 0):
            raise ValueError("Search authority requires a current source incarnation")

    def to_dict(self):
        return {"ref": self.ref, "identity": self.identity, "controller": self.controller,
                "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, Mapping) or set(raw) != {"ref", "identity", "controller", "timestamp"}:
            raise ValueError("Search-control source fields changed")
        return cls(**raw)

    def live_in(self, state):
        player = state.players.get(self.controller)
        if player is None or not player.in_game:
            return False
        return any(
            (card.ref, card.logical_object_id, card.controller, card.zone_timestamp)
            == (self.ref, self.identity, self.controller, self.timestamp)
            for oid in player.zones["battlefield"] if (card := state.cards.get(oid)) is not None
            and card.zone == "battlefield" and not card.phased_out
        )


@dataclass(frozen=True, slots=True)
class LibrarySearchControl:
    stack: str
    searcher: str
    controller: SearchControlSource | None
    replacements: tuple[SearchControlSource, ...]

    def __post_init__(self):
        if (type(self.stack) is not str or not self.stack or type(self.searcher) is not str or not self.searcher
                or not isinstance(self.replacements, tuple)
                or any(not isinstance(value, SearchControlSource) or value.controller == self.searcher
                       for value in self.replacements)
                or self.controller is not None and (
                    not isinstance(self.controller, SearchControlSource) or self.controller.controller == self.searcher
                ) or not (self.controller is not None or self.replacements)
                or len({value.ref for value in self.replacements}) != len(self.replacements)):
            raise ValueError("Search control requires the original searcher and distinct opposing sources")

    def to_dict(self):
        return {"schema_version": 1, "stack": self.stack, "searcher": self.searcher,
                "controller": self.controller.to_dict() if self.controller is not None else None,
                "replacements": [source.to_dict() for source in self.replacements]}

    @classmethod
    def from_dict(cls, raw):
        if (not isinstance(raw, Mapping) or set(raw) != {"schema_version", "stack", "searcher", "controller", "replacements"}
                or type(raw["schema_version"]) is not int or raw["schema_version"] != 1
                or not isinstance(raw["replacements"], (list, tuple))):
            raise ValueError("Search control requires its exact pending decision data")
        controller = SearchControlSource.from_dict(raw["controller"]) if raw["controller"] is not None else None
        return cls(raw["stack"], raw["searcher"], controller,
                   tuple(SearchControlSource.from_dict(source) for source in raw["replacements"]))

    def live_in(self, state, actor):
        player = state.players.get(actor)
        return (actor == self.searcher and player is not None and player.in_game
                and bool(state.stack and state.stack[-1].ref == self.stack)
                and (self.controller is None or self.controller.live_in(state))
                and all(source.live_in(state) for source in self.replacements))


def pending_library_search_control(state, decision, actor):
    if decision is None or decision.kind not in CONTROL_KINDS:
        return None
    raw = decision.continuation.get(CONTROL_FIELD)
    if raw is None:
        return None
    try:
        control = LibrarySearchControl.from_dict(raw)
    except (TypeError, ValueError, KeyError):
        return None
    if control.live_in(state, actor):
        return control
    return None
