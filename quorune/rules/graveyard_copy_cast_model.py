from __future__ import annotations

"""Closed instructions for exiling graveyard targets and casting their copies."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap, freeze_value
from .library_selection import LibrarySelectionObjectIdentity

GRAVEYARD_COPY_OPERATION = "exile_graveyard_cards_and_cast_copies"
GRAVEYARD_COPY_OFFER = "offer_graveyard_card_copies"
GRAVEYARD_COPY_CAPABILITY = "casting.resolution.graveyard_copies"
GRAVEYARD_COPY_MECHANIC = "resolving-graveyard-copy-cast"
GRAVEYARD_COPY_PARENT_FIELD = "resolving_graveyard_copy_parent"
GRAVEYARD_COPY_RULES = (
    "118.8c", "400.7j", "601.2", "608.2b", "608.2g", "608.3f", "704.5e", "707.10f", "707.12", "707.12a",
)
_SCOPES = ("any", "opponent", "paired", "own", "own_all", "own_source_power", "public_source_power")


def _copy_count(scope, count):
    return type(count) is int and (count == 1 or scope == "any" and count == 3)


def validate_graveyard_copy_effect(effect, *, resolved=False):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", "scope", "copies", "cards"}
            or effect["op"] != GRAVEYARD_COPY_OPERATION or effect["scope"] not in _SCOPES
            or not _copy_count(effect["scope"], effect["copies"])):
        raise ValueError("The graveyard copy instruction changed")
    cards = effect["cards"]
    if resolved:
        if (type(effect["player"]) is not str or not effect["player"] or not isinstance(cards, (list, tuple))
                or any(type(ref) is not str or not ref for ref in cards) or len(set(cards)) != len(cards)):
            raise ValueError("The graveyard copy instruction requires distinct current targets")
    elif effect["player"] != "$controller" or (cards not in ([], ()) if effect["scope"] == "own_all" else cards != "$targets"):
        raise ValueError("The graveyard copy instruction requires its declared target group")
    if effect["scope"] == "own_all" and tuple(cards):
        raise ValueError("An all-graveyard instruction does not announce targets")


@dataclass(frozen=True, slots=True)
class ExileGraveyardCopiesIntent:
    actor: str
    stack_ref: str
    scope: str
    copies: int
    originals: tuple[LibrarySelectionObjectIdentity, ...]
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.actor, self.stack_ref))
                or self.scope not in _SCOPES or not _copy_count(self.scope, self.copies)
                or not isinstance(self.originals, tuple) or not self.originals
                or any(not isinstance(value, LibrarySelectionObjectIdentity) for value in self.originals)
                or len({value.object_id for value in self.originals}) != len(self.originals)
                or self.scope != "own_all" and len(self.originals) > (2 if self.scope == "paired" else 1)):
            raise ValueError("Graveyard copy preparation requires its exact original cards")
        selections = freeze_value(self.replacement_selections, field="graveyard copy exile replacements")
        if not isinstance(selections, tuple) or any(
            not isinstance(selection, FrozenMap) and (type(selection) is not str or not selection)
            for selection in selections
        ):
            raise ValueError("Graveyard copy exile replacements are malformed")
        object.__setattr__(self, "replacement_selections", selections)

    def identity(self):
        return {"actor": self.actor, "stack_ref": self.stack_ref, "scope": self.scope, "copies": self.copies,
                "originals": [card.to_dict() for card in self.originals]}


def decode_exile_graveyard_copies_identity(value):
    if (not isinstance(value, Mapping) or set(value) != {"actor", "stack_ref", "scope", "copies", "originals"}
            or not isinstance(value["originals"], (list, tuple))):
        raise ValueError("Graveyard copy exile continuation identity is malformed")
    return ExileGraveyardCopiesIntent(
        value["actor"], value["stack_ref"], value["scope"], value["copies"],
        tuple(LibrarySelectionObjectIdentity.from_dict(card) for card in value["originals"]),
    ).identity()
