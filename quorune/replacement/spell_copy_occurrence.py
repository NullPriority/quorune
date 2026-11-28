"""Replacement planning for one positive spell-copy occurrence."""

from __future__ import annotations

from dataclasses import dataclass

from . import (
    AddAmount, ReplaceableEvent, ReplacementClass, ReplacementEffect, ReplacementEventBatch,
    advance_replacement_batch,
)

COPY_EVENT = "stack.copy.spell"


@dataclass(frozen=True, slots=True)
class AdditionalSpellCopySource:
    incarnation: str
    ability: str
    controller: str

    def __post_init__(self) -> None:
        if any(type(field) is not str or not field for field in (self.incarnation, self.ability, self.controller)):
            raise ValueError("A spell-copy replacement needs its original source, ability and controller")

    @property
    def effect_id(self) -> str:
        return f"additional-spell-copy:{len(self.incarnation)}:{self.incarnation}:{self.ability}"

    def effect(self) -> ReplacementEffect:
        return ReplacementEffect(
            effect_id=self.effect_id, source_id=self.incarnation, event_kind=COPY_EVENT,
            replacement_class=ReplacementClass.OTHER,
            conditions={"controller": {"eq": self.controller}},
            operations=(AddAmount("count", 1),),
            label="Copy the spell one additional time",
        )


@dataclass(frozen=True, slots=True)
class SpellCopyOccurrence:
    event_id: str
    controller: str
    original_count: int

    def __post_init__(self) -> None:
        if type(self.original_count) is not int or self.original_count < 0:
            raise ValueError("A spell-copy occurrence requires a nonnegative original count")
        if any(type(field) is not str or not field for field in (self.event_id, self.controller)):
            raise ValueError("A spell-copy occurrence requires an identity and controller")

    def advance(self, sources, seats, selections=()):
        """Apply each eligible replacement once at the original event's boundary."""
        if not self.original_count:
            if selections:
                raise ValueError("An empty copy occurrence has no replacement selections")
            return None
        event = ReplaceableEvent(
            event_id=self.event_id, kind=COPY_EVENT, affected_player=self.controller,
            payload={"controller": self.controller, "count": self.original_count},
        )
        return advance_replacement_batch(
            ReplacementEventBatch(f"replacement:{self.event_id}", (event,), tuple(seats)),
            tuple(source.effect() for source in sources), selections=tuple(selections),
        )


def ordered_spell_copy_roles(original_count, replacement_ids, order):
    """Seal the controller's bottom-to-top order of original and added copies."""
    if type(original_count) is not int or original_count < 1:
        raise ValueError("Copy placement requires a positive original copy count")
    ids = tuple(replacement_ids)
    if any(type(effect_id) is not str or not effect_id for effect_id in ids):
        raise ValueError("Additional copies require the identities of the applied replacements")
    originals = tuple(f"original:{index}" for index in range(original_count))
    additions = tuple(f"additional:{effect_id}" for effect_id in ids)
    expected = (*originals, *additions)
    labels = tuple(order)
    if (any(type(label) is not str for label in labels)
            or len(set(expected)) != len(expected) or len(labels) != len(expected) or set(labels) != set(expected)):
        raise ValueError("Order every original and additional copy exactly once")
    if tuple(label for label in labels if label.startswith("original:")) != originals:
        raise ValueError("Keep the relative order already chosen for the original copies")
    return labels
