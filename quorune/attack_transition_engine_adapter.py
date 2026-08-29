from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .ability_fragments import (
    AbilityFragmentError,
    CombatKeywordTriggerKind,
    canonical_ability_fragments,
    combat_keyword_trigger_specs,
)
from .attack_transition_model import (
    ATTACK_TRIGGER_KINDS,
    AttackKeywordTriggerOccurrence,
    AttackRecipient,
    AttackRecipientKind,
    AttackTransitionError,
    AttackTransitionParticipant,
    AttackTransitionQuery,
    build_attack_transition,
    derive_attack_keyword_trigger_occurrences,
)
from .attack_counter_triggers import (
    ATTACK_COUNTER_TRIGGER_SEMANTIC_KEY,
    AttackCounterTriggerOccurrence,
    AttackPlayerLifeSnapshot,
    PlayerLifeTotal,
    attack_counter_effect,
    attack_counter_trigger_stack_item,
    derive_attack_counter_trigger_occurrences,
)
from .attack_transition_resolution import (
    ATTACK_KEYWORD_TRIGGER_SEMANTIC_KEY,
    attack_keyword_trigger_stack_item,
    resolve_attack_keyword_trigger,
)
from .combat_relationship_state import (
    AttackDeclarationAssignment,
    CombatRelationshipStateError,
    commit_attack_declaration,
)
from .errors import StateInvariantError
from .mentor import (
    MENTOR_TRIGGER_SEMANTIC_KEY,
    MentorTriggerOccurrence,
    derive_mentor_trigger_occurrences,
    mentor_counter_effect,
    mentor_trigger_stack_item,
)
from .keyword_abilities import normalized_characteristic_keywords


class EngineAttackTransitionQuery(AttackTransitionQuery):
    """Narrow read-only adapter over the authoritative combat facade."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._fragment_cache: dict[str, tuple[Any, ...]] = {}
        self._training_power_required: bool | None = None
        self._attacker_ids: tuple[str, ...] | None = None

    def turn_sequence(self) -> int:
        return self._engine.state.turn_sequence

    def priority_epoch(self) -> int:
        return self._engine.state.priority_epoch

    def active_player(self) -> str:
        active = self._engine.state.active_player
        if type(active) is not str or not active:
            raise AttackTransitionError(
                "An attack transition requires an active player"
            )
        return active

    def attacker_object_ids(self) -> Sequence[str]:
        if self._attacker_ids is None:
            self._attacker_ids = tuple(self._engine.state.combat.attackers)
        return self._attacker_ids

    def trigger_source_object_ids(self) -> Sequence[str]:
        active = self.active_player()
        source_ids = []
        for object_id in self._engine.state.players[active].zones["battlefield"]:
            participant = self.participant(object_id)
            if participant.trigger_specs:
                source_ids.append(object_id)
        return tuple(source_ids)

    def _fragments(self, object_id: str) -> tuple[Any, ...]:
        cached = self._fragment_cache.get(object_id)
        if cached is not None:
            return cached
        card = self._engine.state.cards.get(object_id)
        if (
            card is None
            or card.zone != "battlefield"
            or card.phased_out
            or card.controller != self.active_player()
        ):
            raise AttackTransitionError(
                "Every attack-transition participant must be a current "
                "active-player permanent"
            )
        try:
            fragments = canonical_ability_fragments(
                self._engine._effective_card_data(card).get(
                    "ability_fragments", ()
                )
            )
        except AbilityFragmentError as exc:
            raise AttackTransitionError(str(exc)) from exc
        self._fragment_cache[object_id] = fragments
        return fragments

    def _requires_training_power_snapshot(self) -> bool:
        if self._training_power_required is None:
            self._training_power_required = any(
                spec.kind is CombatKeywordTriggerKind.TRAINING
                for object_id in self.attacker_object_ids()
                for spec in combat_keyword_trigger_specs(
                    self._fragments(object_id)
                )
            )
        return self._training_power_required

    def player_life_snapshot(self) -> AttackPlayerLifeSnapshot:
        return AttackPlayerLifeSnapshot(
            tuple(
                PlayerLifeTotal(
                    player=seat,
                    life=self._engine.state.players[seat].life,
                )
                for seat in self._engine.active_seats
            )
        )

    def participant(self, object_id: str) -> AttackTransitionParticipant:
        card = self._engine.state.cards.get(object_id)
        if (
            card is None
            or card.zone != "battlefield"
            or card.phased_out
            or card.controller != self.active_player()
        ):
            raise AttackTransitionError(
                "Every attack-transition participant must be a current "
                "active-player permanent"
            )
        effective = self._engine._effective_card_data(card)
        card_types, _subtypes, _supertypes = self._engine._type_parts(
            str(effective.get("type_line") or "")
        )
        fragments = self._fragments(object_id)
        trigger_specs = tuple(
            spec
            for spec in combat_keyword_trigger_specs(fragments)
            if spec.kind in ATTACK_TRIGGER_KINDS
        )
        return AttackTransitionParticipant(
            object_id=card.object_id,
            logical_object_id=card.logical_object_id,
            reference=card.ref,
            controller=card.controller,
            is_creature="creature" in card_types and "battle" not in card_types,
            trigger_specs=trigger_specs,
            keywords=tuple(normalized_characteristic_keywords(effective)),
            power=(
                self._engine._numeric_stat(card.object_id, "power")
                if card.object_id in self.attacker_object_ids()
                and (
                    self._requires_training_power_snapshot()
                    or any(
                        spec.kind is CombatKeywordTriggerKind.MENTOR
                        for spec in trigger_specs
                    )
                )
                else None
            ),
        )

    def recipient(self, attacker_object_id: str) -> AttackRecipient:
        context = self._engine.state.combat.attack_target_context.get(
            attacker_object_id
        )
        if not isinstance(context, Mapping):
            raise AttackTransitionError(
                "Every declared attacker requires public recipient context"
            )
        expected = {"target", "kind", "defending_player"}
        if context.get("kind") != "player":
            expected.add("logical_object_id")
        if set(context) != expected:
            raise AttackTransitionError(
                "Attack recipient context has a closed field set"
            )
        try:
            kind = AttackRecipientKind(str(context["kind"]))
        except ValueError as exc:
            raise AttackTransitionError(
                "Unsupported attack-recipient kind"
            ) from exc
        return AttackRecipient(
            kind=kind,
            reference=context["target"],
            defending_player=context["defending_player"],
            logical_object_id=context.get("logical_object_id"),
        )


def commit_engine_attack_declaration(
    engine: Any,
    *,
    controller: str,
    chosen: Sequence[tuple[Any, Mapping[str, str]]],
) -> tuple[AttackDeclarationAssignment, ...]:
    """Adapt validated card/recipient pairs to the relationship owner."""

    assignments = tuple(
        AttackDeclarationAssignment(
            attacker_object_id=card.object_id,
            target=details["target"],
            target_kind=details["kind"],
            defending_player=details["defending_player"],
            target_logical_object_id=details.get("logical_object_id"),
        )
        for card, details in chosen
    )
    try:
        return commit_attack_declaration(
            engine.state.combat,
            engine.state.cards,
            controller=controller,
            assignments=assignments,
        )
    except CombatRelationshipStateError as exc:
        raise StateInvariantError(str(exc)) from exc


def attack_transition_stack_items(engine: Any) -> tuple[Any, ...]:
    """Seal the attack declaration and return its represented stack items."""

    try:
        query = EngineAttackTransitionQuery(engine)
        event = build_attack_transition(query)
        if event is None:
            return ()
        occurrences = derive_attack_keyword_trigger_occurrences(event)
        mentor_occurrences = derive_mentor_trigger_occurrences(event)
        counter_occurrences = derive_attack_counter_trigger_occurrences(
            event,
            query.player_life_snapshot(),
        )
        stack_items = []
        for occurrence in occurrences:
            ref = engine._next_ref("S")
            stack_items.append(
                attack_keyword_trigger_stack_item(
                    occurrence,
                    ref=ref,
                    stack_id=engine._stable_runtime_id("stack", ref),
                    visibility=engine.seats,
                )
            )
        for occurrence in mentor_occurrences:
            ref = engine._next_ref("S")
            stack_items.append(
                mentor_trigger_stack_item(
                    occurrence,
                    ref=ref,
                    stack_id=engine._stable_runtime_id("stack", ref),
                    visibility=engine.seats,
                )
            )
        for occurrence in counter_occurrences:
            ref = engine._next_ref("S")
            stack_items.append(
                attack_counter_trigger_stack_item(
                    occurrence,
                    ref=ref,
                    stack_id=engine._stable_runtime_id("stack", ref),
                    visibility=engine.seats,
                )
            )
        semantic_trigger_refs: list[str] = []
        participants = {
            participant.object_id: participant
            for participant in event.participants
        }
        semantic_sources = tuple(
            engine._semantic_event_sources(zones={"battlefield"})
        )
        for assignment in event.assignments:
            source = engine.state.cards.get(
                assignment.attacker_object_id
            )
            if source is None:
                raise AttackTransitionError(
                    "A sealed attacker disappeared before trigger discovery"
                )
            semantic_trigger_refs.extend(
                engine._dispatch_semantic_event(
                    "creature.attacks",
                    {
                        "event_id": event.transition_id,
                        "card": source.ref,
                        "defender": assignment.recipient.reference,
                        "defending_player": (
                            assignment.recipient.defending_player
                        ),
                        "controller": participants[
                            assignment.attacker_object_id
                        ].controller,
                        "types": ["creature"],
                        "keywords": list(
                            participants[
                                assignment.attacker_object_id
                            ].keywords
                        ),
                        "attacking_alone": len(event.assignments) == 1,
                        "attack_transition": event.to_dict(),
                    },
                    sources=semantic_sources,
                    trigger_batch=stack_items,
                )
            )
    except (AbilityFragmentError, AttackTransitionError) as exc:
        raise StateInvariantError(str(exc)) from exc
    engine._log(
        None,
        "combat.attack_transition",
        (
            f"Completed {len(event.assignments)} attack declaration(s) and "
            "created "
            f"{len(stack_items)} "
            "represented "
            "trigger(s)."
        ),
        {
            "transition": event.to_dict(),
            "trigger_occurrences": [
                occurrence.occurrence_id for occurrence in occurrences
            ]
            + [
                occurrence.occurrence_id
                for occurrence in mentor_occurrences
            ]
            + [
                occurrence.occurrence_id
                for occurrence in counter_occurrences
            ],
            "semantic_trigger_refs": semantic_trigger_refs,
        },
        importance=2,
        changed_objects=[
            participant.object_id for participant in event.participants
        ],
    )
    return tuple(stack_items)


def prepare_attack_keyword_trigger_resolution(
    engine: Any,
    item: Any,
) -> bool:
    """Resolve one typed attack occurrence through the engine facade."""

    if item.semantic_key == ATTACK_COUNTER_TRIGGER_SEMANTIC_KEY:
        context = item.context
        if not isinstance(context, Mapping) or context.get("event") != (
            "combat.attack_transition"
        ):
            raise StateInvariantError(
                "An attack-counter trigger has malformed event context"
            )
        try:
            occurrence = AttackCounterTriggerOccurrence.from_dict(
                context.get("attack_counter_trigger")
            )
        except (TypeError, AttackTransitionError) as exc:
            raise StateInvariantError(str(exc)) from exc
        if (
            occurrence.controller != item.controller
            or occurrence.source.object_id != item.source_object_id
        ):
            raise StateInvariantError(
                "An attack-counter trigger no longer matches its stack identity"
            )
        source = engine.state.cards.get(occurrence.source.object_id)
        source_available = (
            source is not None
            and source.zone == "battlefield"
            and not source.phased_out
            and source.logical_object_id == occurrence.source.logical_object_id
        )
        engine._begin_resolve_item(
            item,
            [attack_counter_effect(source.ref)] if source_available else [],
            item.default_destination,
            note=(
                f"Resolved typed {occurrence.kind.value} occurrence"
                if source_available
                else (
                    f"Resolved typed {occurrence.kind.value} occurrence with "
                    "unavailable source"
                )
            ),
        )
        return True
    if item.semantic_key == MENTOR_TRIGGER_SEMANTIC_KEY:
        context = item.context
        if not isinstance(context, Mapping) or context.get("event") != (
            "combat.attack_transition"
        ):
            raise StateInvariantError(
                "A Mentor trigger has malformed event context"
            )
        try:
            occurrence = MentorTriggerOccurrence.from_dict(
                context.get("mentor_trigger")
            )
        except (TypeError, AttackTransitionError) as exc:
            raise StateInvariantError(str(exc)) from exc
        if (
            occurrence.controller != item.controller
            or occurrence.source.object_id != item.source_object_id
        ):
            raise StateInvariantError(
                "A Mentor trigger no longer matches its stack identity"
            )
        engine._begin_resolve_item(
            item,
            [mentor_counter_effect()],
            item.default_destination,
            note="Resolved typed Mentor occurrence",
        )
        return True
    if item.semantic_key != ATTACK_KEYWORD_TRIGGER_SEMANTIC_KEY:
        return False
    context = item.context
    if not isinstance(context, Mapping) or context.get("event") != (
        "combat.attack_transition"
    ):
        raise StateInvariantError(
            "An attack-keyword trigger has malformed event context"
        )
    try:
        occurrence = AttackKeywordTriggerOccurrence.from_dict(
            context.get("attack_keyword_trigger")
        )
    except (TypeError, AttackTransitionError) as exc:
        raise StateInvariantError(str(exc)) from exc
    if (
        occurrence.controller != item.controller
        or occurrence.source.object_id != item.source_object_id
    ):
        raise StateInvariantError(
            "An attack-keyword trigger no longer matches its stack identity"
        )
    try:
        resolve_attack_keyword_trigger(engine, occurrence, stack_ref=item.ref)
    except AttackTransitionError as exc:
        raise StateInvariantError(str(exc)) from exc
    engine._begin_resolve_item(
        item,
        [],
        item.default_destination,
        note=f"Resolved typed {occurrence.kind.value} occurrence",
    )
    return True


__all__ = [
    "EngineAttackTransitionQuery",
    "attack_transition_stack_items",
    "commit_engine_attack_declaration",
    "prepare_attack_keyword_trigger_resolution",
]
