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

    from .rules.banding import expanded_block_pairs
    chosen = expanded_block_pairs(engine, chosen)
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


def _block_event_context(event, participant, **extra):
    return {
        "event_id": event.transition_id, "card": participant.reference,
        "controller": participant.controller, "types": ["creature"],
        "keywords": list(participant.keywords), "block_transition": event.to_dict(),
        **extra,
    }


def _dispatch_block_relationship_triggers(
    engine, event, stack_items, *, put_blocking, already_blocked,
):
    participants = {value.object_id: value for value in event.participants}
    sources = tuple(engine._semantic_event_sources(zones={"battlefield"}))
    refs = []
    blocked = set(already_blocked)
    for assignment in event.assignments:
        blocker = participants[assignment.blocker_object_id]
        attacker = participants[assignment.attacker_object_id]
        if not put_blocking:
            refs.extend(engine._dispatch_semantic_event(
                "creature.blocks", _block_event_context(event, blocker,
                    blocked_attacker=attacker.reference,
                    blocked_attacker_keywords=list(attacker.keywords)),
                sources=sources, trigger_batch=stack_items))
        refs.extend(engine._dispatch_semantic_event(
            "creature.becomes_blocked_by", _block_event_context(event, attacker,
                blocking_creature=blocker.reference,
                blocking_creature_object_id=blocker.object_id,
                blocking_creature_logical_object_id=blocker.logical_object_id,
                blocking_creature_controller=blocker.controller),
            sources=sources, trigger_batch=stack_items))
        if attacker.object_id in blocked:
            continue
        blocked.add(attacker.object_id)
        target = engine.state.combat.attackers.get(attacker.object_id)
        defender = (engine._defending_player_for_attacker(attacker.object_id, target)
                    if isinstance(target, str) else None)
        if defender not in engine.state.players:
            raise BlockTransitionError('A blocked attacker requires one sealed defending player')
        refs.extend(engine._dispatch_semantic_event(
            "creature.becomes_blocked", _block_event_context(event, attacker,
                defending_player=defender), sources=sources, trigger_batch=stack_items))
    return refs


def _block_transition_trigger_items(engine, event, *, put_blocking=False, already_blocked=frozenset()):
    occurrences = derive_block_keyword_trigger_occurrences(
        event, put_blocking=put_blocking, already_blocked=already_blocked)
    items = []
    for occurrence in occurrences:
        ref = engine._next_ref('S')
        items.append(block_keyword_trigger_stack_item(
            occurrence, ref=ref, stack_id=engine._stable_runtime_id('stack', ref),
            visibility=engine.seats))
    from .ring_triggers import ring_block_stack_items
    items.extend(ring_block_stack_items(engine, event))
    refs = _dispatch_block_relationship_triggers(
        engine, event, items, put_blocking=put_blocking, already_blocked=already_blocked)
    return items, occurrences, refs


def enqueue_block_transition_triggers(engine: Any) -> None:
    """Seal one public block event and merge its triggers before priority."""
    try:
        event = build_block_transition(EngineBlockTransitionQuery(engine))
        if event is None:
            return
        items, occurrences, refs = _block_transition_trigger_items(engine, event)
    except (AbilityFragmentError, BlockTransitionError) as exc:
        raise StateInvariantError(str(exc)) from exc
    engine._log(None, 'combat.block_transition',
        f'Completed {len(event.assignments)} block relationship(s) and '
        f'created {len(occurrences)} represented trigger(s).',
        {'transition': event.to_dict(),
         'trigger_occurrences': [value.occurrence_id for value in occurrences],
         'semantic_trigger_refs': refs}, importance=2,
        changed_objects=[value.object_id for value in event.participants])
    enqueue_trigger_batch(engine, items)


class _PutBlockingQuery(EngineBlockTransitionQuery):
    def __init__(self, engine, assignments):
        super().__init__(engine)
        self._relationships = {}
        for assignment in assignments:
            self._relationships.setdefault(assignment.attacker_object_id, []).append(
                assignment.blocker_object_id)

    def attacker_object_ids(self):
        return tuple(self._relationships)

    def blocker_object_ids(self, attacker_object_id):
        return tuple(self._relationships[attacker_object_id])


def _put_blocking_assignments(engine, controller, assignments):
    if type(controller) is not str or controller not in engine.active_seats:
        raise CombatRelationshipStateError('Put-blocking controller must be active')
    values = tuple(assignments)
    if any(not isinstance(value, BlockDeclarationAssignment) for value in values):
        raise CombatRelationshipStateError('Put-blocking requires typed relationship assignments')
    pairs = [(value.blocker_object_id, value.attacker_object_id) for value in values]
    if len(pairs) != len(set(pairs)):
        raise CombatRelationshipStateError('Put-blocking assignments cannot repeat a pair')
    eligible = []
    for value in values:
        blocker = engine.state.cards.get(value.blocker_object_id)
        if blocker is None:
            raise CombatRelationshipStateError('Put-blocking requires a known entering creature')
        attacker = engine.state.cards.get(value.attacker_object_id)
        target = engine.state.combat.attackers.get(value.attacker_object_id)
        if (attacker is None or not isinstance(target, str)
                or blocker.zone != 'battlefield' or blocker.phased_out
                or blocker.controller != controller
                or attacker.zone != 'battlefield' or attacker.phased_out
                or engine._defending_player_for_attacker(attacker.object_id, target) != controller):
            continue
        if any('creature' not in engine._type_parts(
            str(engine._effective_card_data(card).get('type_line') or ''))[0]
               for card in (attacker, blocker)):
            continue
        if blocker.object_id not in engine.state.combat.blockers.get(attacker.object_id, ()):
            eligible.append(value)
    return tuple(eligible)


def commit_engine_put_blocking(engine: Any, *, controller: str, assignments):
    """Commit specified post-entry blocks under CR509.4, not a declaration.

    The entry owner supplies already-entered objects and a specified attacker.
    This primitive does not choose an attacker or expose a client command.
    """
    try:
        eligible = _put_blocking_assignments(engine, controller, assignments)
        if not eligible:
            return ()
        event = build_block_transition(_PutBlockingQuery(engine, eligible))
        already_blocked = frozenset(engine.state.combat.blockers)
        committed = commit_block_declaration(engine.state.combat, engine.state.cards,
            controller=controller, assignments=eligible)
        items, _occurrences, _refs = _block_transition_trigger_items(
            engine, event, put_blocking=True, already_blocked=already_blocked)
    except (AbilityFragmentError, BlockTransitionError, CombatRelationshipStateError) as exc:
        raise StateInvariantError(str(exc)) from exc
    enqueue_trigger_batch(engine, items)
    return committed


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
    "commit_engine_put_blocking",
    "enqueue_block_transition_triggers",
    "prepare_block_keyword_trigger_resolution",
]
