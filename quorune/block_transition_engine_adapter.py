from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .ability_fragments import (
    AbilityFragmentError,
    canonical_ability_fragments,
    combat_keyword_trigger_specs,
)
from .block_transitions import (
    BLOCK_KEYWORD_TRIGGER_SEMANTIC_KEY,
    BlockKeywordTriggerOccurrence,
    BlockTransitionError,
    BlockTransitionParticipant,
    BlockTransitionQuery,
    block_keyword_trigger_stack_item,
    build_block_transition,
    derive_block_keyword_trigger_occurrences,
    resolve_block_keyword_trigger,
)
from .errors import StateInvariantError
from .combat_relationship_state import (
    BlockDeclarationAssignment,
    CombatRelationshipStateError,
    commit_block_declaration,
)
from .trigger_processing import enqueue_trigger_batch
from .keyword_abilities import normalized_characteristic_keywords


class EngineBlockTransitionQuery(BlockTransitionQuery):
    """Narrow read-only adapter over the authoritative combat facade."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def turn_sequence(self) -> int:
        return self._engine.state.turn_sequence

    def priority_epoch(self) -> int:
        return self._engine.state.priority_epoch

    def active_player(self) -> str:
        active = self._engine.state.active_player
        if type(active) is not str or not active:
            raise BlockTransitionError(
                "A block transition requires an active player"
            )
        return active

    def attacker_object_ids(self) -> Sequence[str]:
        return tuple(self._engine.state.combat.attackers)

    def blocker_object_ids(self, attacker_object_id: str) -> Sequence[str]:
        return tuple(
            self._engine.state.combat.blockers.get(attacker_object_id, ())
        )

    def participant(self, object_id: str) -> BlockTransitionParticipant:
        card = self._engine.state.cards.get(object_id)
        if card is None or card.zone != "battlefield" or card.phased_out:
            raise BlockTransitionError(
                "Every block-transition participant must remain on the battlefield"
            )
        effective = self._engine._effective_card_data(card)
        card_types, _subtypes, _supertypes = self._engine._type_parts(
            str(effective.get("type_line") or "")
        )
        if "creature" not in card_types or "battle" in card_types:
            raise BlockTransitionError(
                "Every block-transition participant must be a creature"
            )
        try:
            fragments = canonical_ability_fragments(
                effective.get("ability_fragments", ())
            )
        except AbilityFragmentError as exc:
            raise BlockTransitionError(str(exc)) from exc
        return BlockTransitionParticipant(
            object_id=card.object_id,
            logical_object_id=card.logical_object_id,
            reference=card.ref,
            controller=card.controller,
            trigger_specs=combat_keyword_trigger_specs(fragments),
            keywords=tuple(normalized_characteristic_keywords(effective)),
        )


def commit_engine_block_declaration(
    engine: Any,
    *,
    controller: str,
    chosen: Sequence[tuple[Any, Any]],
) -> tuple[BlockDeclarationAssignment, ...]:
    """Adapt validated card pairs to the typed relationship mutation owner."""

    try:
        return commit_block_declaration(
            engine.state.combat,
            engine.state.cards,
            controller=controller,
            assignments=tuple(
                BlockDeclarationAssignment(
                    blocker_object_id=blocker.object_id,
                    attacker_object_id=attacker.object_id,
                )
                for blocker, attacker in chosen
            ),
        )
    except CombatRelationshipStateError as exc:
        raise StateInvariantError(str(exc)) from exc


def enqueue_block_transition_triggers(engine: Any) -> None:
    """Seal one public block event and merge its triggers before priority."""

    try:
        event = build_block_transition(EngineBlockTransitionQuery(engine))
        if event is None:
            return
        occurrences = derive_block_keyword_trigger_occurrences(event)
        stack_items = []
        for occurrence in occurrences:
            ref = engine._next_ref("S")
            stack_items.append(
                block_keyword_trigger_stack_item(
                    occurrence,
                    ref=ref,
                    stack_id=engine._stable_runtime_id("stack", ref),
                    visibility=engine.seats,
                )
            )
        participants = {
            participant.object_id: participant
            for participant in event.participants
        }
        semantic_sources = tuple(
            engine._semantic_event_sources(zones={"battlefield"})
        )
        semantic_trigger_refs: list[str] = []
        blocked_attackers: set[str] = set()
        for assignment in event.assignments:
            blocker = participants[assignment.blocker_object_id]
            blocked_attacker = participants[assignment.attacker_object_id]
            semantic_trigger_refs.extend(
                engine._dispatch_semantic_event(
                    "creature.blocks",
                    {
                        "event_id": event.transition_id,
                        "card": blocker.reference,
                        "controller": blocker.controller,
                        "types": ["creature"],
                        "keywords": list(blocker.keywords),
                        "blocked_attacker": blocked_attacker.reference,
                        "blocked_attacker_keywords": list(
                            blocked_attacker.keywords
                        ),
                        "block_transition": event.to_dict(),
                    },
                    sources=semantic_sources,
                    trigger_batch=stack_items,
                )
            )
            if assignment.attacker_object_id in blocked_attackers:
                continue
            blocked_attackers.add(assignment.attacker_object_id)
            attacker = participants[assignment.attacker_object_id]
            semantic_trigger_refs.extend(
                engine._dispatch_semantic_event(
                    "creature.becomes_blocked",
                    {
                        "event_id": event.transition_id,
                        "card": attacker.reference,
                        "controller": attacker.controller,
                        "types": ["creature"],
                        "keywords": list(attacker.keywords),
                        "block_transition": event.to_dict(),
                    },
                    sources=semantic_sources,
                    trigger_batch=stack_items,
                )
            )
    except (AbilityFragmentError, BlockTransitionError) as exc:
        raise StateInvariantError(str(exc)) from exc
    engine._log(
        None,
        "combat.block_transition",
        (
            f"Completed {len(event.assignments)} block relationship(s) and "
            f"created {len(occurrences)} represented trigger(s)."
        ),
        {
            "transition": event.to_dict(),
            "trigger_occurrences": [
                occurrence.occurrence_id for occurrence in occurrences
            ],
            "semantic_trigger_refs": semantic_trigger_refs,
        },
        importance=2,
        changed_objects=[
            participant.object_id for participant in event.participants
        ],
    )
    enqueue_trigger_batch(engine, stack_items)


def prepare_block_keyword_trigger_resolution(
    engine: Any,
    item: Any,
) -> bool:
    """Resolve one typed occurrence and hand stack completion to the facade."""

    if item.semantic_key != BLOCK_KEYWORD_TRIGGER_SEMANTIC_KEY:
        return False
    context = item.context
    if not isinstance(context, Mapping) or context.get("event") != (
        "combat.block_transition"
    ):
        raise StateInvariantError(
            "A block-keyword trigger has malformed event context"
        )
    raw = context.get("block_keyword_trigger")
    try:
        occurrence = BlockKeywordTriggerOccurrence.from_dict(raw)
    except (TypeError, BlockTransitionError) as exc:
        raise StateInvariantError(str(exc)) from exc
    if (
        occurrence.controller != item.controller
        or occurrence.source.object_id != item.source_object_id
    ):
        raise StateInvariantError(
            "A block-keyword trigger no longer matches its stack identity"
        )
    try:
        resolve_block_keyword_trigger(engine, occurrence, stack_ref=item.ref)
    except BlockTransitionError as exc:
        raise StateInvariantError(str(exc)) from exc
    engine._begin_resolve_item(
        item,
        [],
        item.default_destination,
        note=f"Resolved typed {occurrence.kind.value} occurrence",
    )
    return True


__all__ = [
    "EngineBlockTransitionQuery",
    "commit_engine_block_declaration",
    "enqueue_block_transition_triggers",
    "prepare_block_keyword_trigger_resolution",
]
