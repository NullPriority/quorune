from __future__ import annotations

"""Normalized public spell-copy occurrence adapter."""

from typing import Any, Mapping, Protocol, Sequence

from .errors import StateInvariantError
from .model import CardInstance, StackItem
from .rules.spell_cast_events import SpellCopyEvent


class SpellCopyEventHost(Protocol):
    state: Any

    def _effective_card_data(
        self, card: str | CardInstance
    ) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _semantic_event_sources(
        self, *, zones: set[str] | None = None
    ) -> list[CardInstance]: ...

    def _dispatch_semantic_event(
        self,
        event: str,
        context: Mapping[str, Any],
        *,
        sources: Sequence[CardInstance] | None = None,
        trigger_batch: list[StackItem] | None = None,
    ) -> list[str]: ...


def dispatch_normalized_spell_copy_event(
    host: SpellCopyEventHost,
    copied: StackItem,
    copied_from_stack_ref: str,
) -> list[str]:
    """Dispatch one typed occurrence after the canonical copy commit."""

    if copied.kind != "spell_copy":
        return []
    if not copied.card_object_id:
        raise StateInvariantError(
            "A normalized spell-copy occurrence requires a spell-copy object"
        )
    copy_object = host.state.cards.get(copied.card_object_id)
    if copy_object is None or not copy_object.is_spell_copy:
        raise StateInvariantError(
            "A normalized spell-copy occurrence lost its stack object"
        )
    types, _, _ = host._type_parts(
        str(host._effective_card_data(copy_object).get("type_line") or "")
    )
    context = SpellCopyEvent(
        card_ref=copy_object.ref,
        object_id=copy_object.object_id,
        logical_object_id=copy_object.logical_object_id,
        controller=copied.controller,
        stack_ref=copied.ref,
        copied_from_stack_ref=copied_from_stack_ref,
        types=tuple(types),
        targets=tuple(copied.targets),
    ).to_context()
    return host._dispatch_semantic_event(
        "spell.cast_or_copy",
        context,
        sources=host._semantic_event_sources(zones={"battlefield"}),
    )


__all__ = ["dispatch_normalized_spell_copy_event"]
