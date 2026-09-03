from __future__ import annotations

import copy
from types import SimpleNamespace
import unittest

from quorune.ability_fragments import (
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
)
from quorune.block_transitions import (
    BlockKeywordTriggerOccurrence,
    BlockTransitionError,
    BlockTransitionEvent,
    BlockTransitionParticipant,
    block_keyword_trigger_stack_item,
    build_block_transition,
    derive_block_keyword_trigger_occurrences,
    resolve_block_keyword_trigger,
)
from quorune.combat_relationship_state import (
    BlockDeclarationAssignment,
    CombatRelationshipStateError,
    commit_block_declaration,
)
from quorune.model import CombatState


def _spec(
    kind: CombatKeywordTriggerKind,
    amount: int = 1,
) -> CombatKeywordTriggerSpec:
    return CombatKeywordTriggerSpec(kind=kind, amount=amount)


def _participant(
    reference: str,
    controller: str,
    *specs: CombatKeywordTriggerSpec,
) -> BlockTransitionParticipant:
    return BlockTransitionParticipant(
        object_id=f"object:{reference}",
        logical_object_id=f"logical:{reference}",
        reference=reference,
        controller=controller,
        trigger_specs=specs,
    )


class _Query:
    def __init__(self, participants, attacks, *, reverse=False):
        self._participants = {
            value.object_id: value for value in participants
        }
        self._attacks = dict(attacks)
        self._reverse = reverse

    def turn_sequence(self):
        return 7

    def priority_epoch(self):
        return 11

    def active_player(self):
        return "A"

    def attacker_object_ids(self):
        values = list(self._attacks)
        return tuple(reversed(values)) if self._reverse else tuple(values)

    def blocker_object_ids(self, attacker_object_id):
        values = list(self._attacks[attacker_object_id])
        return tuple(reversed(values)) if self._reverse else tuple(values)

    def participant(self, object_id):
        return self._participants[object_id]


class BlockTransitionModelTests(unittest.TestCase):
    def setUp(self):
        self.attacker = _participant(
            "A1",
            "A",
            _spec(CombatKeywordTriggerKind.FLANKING),
            _spec(CombatKeywordTriggerKind.FLANKING),
            _spec(CombatKeywordTriggerKind.BUSHIDO, 2),
        )
        self.blocker = _participant(
            "C1",
            "C",
            _spec(CombatKeywordTriggerKind.BUSHIDO, 1),
        )

    def event(self, *, reverse=False):
        event = build_block_transition(
            _Query(
                (self.attacker, self.blocker),
                {self.attacker.object_id: (self.blocker.object_id,)},
                reverse=reverse,
            )
        )
        assert event is not None
        return event

    def test_canonical_transition_is_independent_of_query_order(self):
        other_attacker = _participant("A2", "A")
        other_blocker = _participant("B1", "B")
        participants = (
            self.attacker,
            self.blocker,
            other_attacker,
            other_blocker,
        )
        attacks = {
            self.attacker.object_id: (self.blocker.object_id,),
            other_attacker.object_id: (other_blocker.object_id,),
        }

        first = build_block_transition(_Query(participants, attacks))
        second = build_block_transition(
            _Query(participants, attacks, reverse=True)
        )

        self.assertEqual(first, second)
        self.assertEqual(first.transition_id, second.transition_id)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_flanking_triggers_once_per_instance_and_nonflanking_blocker(self):
        occurrences = derive_block_keyword_trigger_occurrences(self.event())
        flanking = tuple(
            value
            for value in occurrences
            if value.kind is CombatKeywordTriggerKind.FLANKING
        )

        self.assertEqual(2, len(flanking))
        self.assertEqual((0, 1), tuple(value.instance_index for value in flanking))
        self.assertTrue(
            all(value.affected.reference == "C1" for value in flanking)
        )
        self.assertTrue(
            all(value.power_delta == -1 for value in flanking)
        )

    def test_flanking_does_not_trigger_for_a_blocker_with_flanking(self):
        blocker = _participant(
            "C1",
            "C",
            _spec(CombatKeywordTriggerKind.FLANKING),
        )
        event = build_block_transition(
            _Query(
                (self.attacker, blocker),
                {self.attacker.object_id: (blocker.object_id,)},
            )
        )
        assert event is not None

        occurrences = derive_block_keyword_trigger_occurrences(event)

        self.assertFalse(
            any(
                value.kind is CombatKeywordTriggerKind.FLANKING
                for value in occurrences
            )
        )

    def test_bushido_triggers_once_for_blocks_or_becomes_blocked_per_instance(self):
        occurrences = derive_block_keyword_trigger_occurrences(self.event())
        bushido = tuple(
            value
            for value in occurrences
            if value.kind is CombatKeywordTriggerKind.BUSHIDO
        )

        self.assertEqual(2, len(bushido))
        self.assertEqual(
            {("A1", 2), ("C1", 1)},
            {(value.source.reference, value.amount) for value in bushido},
        )
        self.assertTrue(
            all(value.source == value.affected for value in bushido)
        )

    def test_keyword_occurrence_counts_hold_across_bounded_grid(self):
        for flanking_count in range(5):
            for blocker_has_flanking in (False, True):
                for bushido_amount in range(1, 5):
                    with self.subTest(
                        flanking_count=flanking_count,
                        blocker_has_flanking=blocker_has_flanking,
                        bushido_amount=bushido_amount,
                    ):
                        attacker = _participant(
                            "A1",
                            "A",
                            *(
                                _spec(CombatKeywordTriggerKind.FLANKING)
                                for _ in range(flanking_count)
                            ),
                            _spec(
                                CombatKeywordTriggerKind.BUSHIDO,
                                bushido_amount,
                            ),
                        )
                        blocker_specs = [
                            _spec(
                                CombatKeywordTriggerKind.BUSHIDO,
                                bushido_amount + 1,
                            )
                        ]
                        if blocker_has_flanking:
                            blocker_specs.append(
                                _spec(CombatKeywordTriggerKind.FLANKING)
                            )
                        blocker = _participant("B1", "B", *blocker_specs)
                        event = build_block_transition(
                            _Query(
                                (attacker, blocker),
                                {attacker.object_id: (blocker.object_id,)},
                            )
                        )
                        assert event is not None

                        occurrences = derive_block_keyword_trigger_occurrences(
                            event
                        )
                        flanking = tuple(
                            value
                            for value in occurrences
                            if value.kind
                            is CombatKeywordTriggerKind.FLANKING
                        )
                        bushido = tuple(
                            value
                            for value in occurrences
                            if value.kind is CombatKeywordTriggerKind.BUSHIDO
                        )

                        expected_flanking = (
                            0 if blocker_has_flanking else flanking_count
                        )
                        self.assertEqual(expected_flanking, len(flanking))
                        self.assertEqual(
                            {bushido_amount, bushido_amount + 1},
                            {value.amount for value in bushido},
                        )
                        self.assertEqual(
                            event,
                            BlockTransitionEvent.from_dict(event.to_dict()),
                        )

    def test_unblocked_bushido_creature_does_not_trigger(self):
        unblocked = _participant(
            "A2",
            "A",
            _spec(CombatKeywordTriggerKind.BUSHIDO, 4),
        )
        event = build_block_transition(
            _Query(
                (self.attacker, self.blocker, unblocked),
                {
                    self.attacker.object_id: (self.blocker.object_id,),
                    unblocked.object_id: (),
                },
            )
        )
        assert event is not None

        occurrences = derive_block_keyword_trigger_occurrences(event)

        self.assertNotIn(
            "A2", {value.source.reference for value in occurrences}
        )

    def test_malformed_transition_and_occurrence_fail_closed(self):
        event = self.event()
        malformed_event = copy.deepcopy(event.to_dict())
        malformed_event["assignments"].append(
            copy.deepcopy(malformed_event["assignments"][0])
        )
        with self.assertRaises(BlockTransitionError):
            BlockTransitionEvent.from_dict(malformed_event)

        occurrence = derive_block_keyword_trigger_occurrences(event)[0]
        malformed_occurrence = copy.deepcopy(occurrence.to_dict())
        malformed_occurrence["amount"] = True
        with self.assertRaises(BlockTransitionError):
            BlockKeywordTriggerOccurrence.from_dict(malformed_occurrence)
        unknown = copy.deepcopy(occurrence.to_dict())
        unknown["arbitrary"] = "execute"
        with self.assertRaises(BlockTransitionError):
            BlockKeywordTriggerOccurrence.from_dict(unknown)

    def test_block_participant_keywords_round_trip_and_fail_closed(self):
        participant = BlockTransitionParticipant(
            object_id="blocker-a",
            logical_object_id="logical:blocker-a",
            reference="B01",
            controller="B",
            keywords=("Flying", "Defender"),
        )

        self.assertEqual(("defender", "flying"), participant.keywords)
        self.assertEqual(
            participant,
            BlockTransitionParticipant.from_dict(participant.to_dict()),
        )
        malformed = participant.to_dict()
        malformed["keywords"] = "flying"
        with self.assertRaises(BlockTransitionError):
            BlockTransitionParticipant.from_dict(malformed)
        for keywords in ((None,), ("",), ("Flying", "flying"), "flying"):
            with self.subTest(keywords=keywords):
                with self.assertRaises(BlockTransitionError):
                    BlockTransitionParticipant(
                        object_id="blocker-a",
                        logical_object_id="logical:blocker-a",
                        reference="B01",
                        controller="B",
                        keywords=keywords,
                    )

    def test_stack_item_round_trips_the_exact_occurrence(self):
        occurrence = derive_block_keyword_trigger_occurrences(self.event())[0]

        item = block_keyword_trigger_stack_item(
            occurrence,
            ref="S1",
            stack_id="stack:S1",
            visibility=("A", "B", "C", "D"),
        )
        restored = BlockKeywordTriggerOccurrence.from_dict(
            item.context["block_keyword_trigger"]
        )

        self.assertEqual(occurrence, restored)
        self.assertEqual(occurrence.occurrence_id, restored.occurrence_id)


class BlockTransitionCommitAndResolutionTests(unittest.TestCase):
    @staticmethod
    def card(reference: str, controller: str):
        return SimpleNamespace(
            object_id=f"object:{reference}",
            logical_object_id=f"logical:{reference}",
            ref=reference,
            controller=controller,
            zone="battlefield",
            phased_out=False,
            blocking=None,
        )

    def test_relationship_commit_uses_one_typed_mutation_owner(self):
        attacker = self.card("A1", "A")
        blocker = self.card("B1", "B")
        combat = CombatState(attackers={attacker.object_id: "B"})

        committed = commit_block_declaration(
            combat,
            {
                attacker.object_id: attacker,
                blocker.object_id: blocker,
            },
            controller="B",
            assignments=(
                BlockDeclarationAssignment(
                    blocker_object_id=blocker.object_id,
                    attacker_object_id=attacker.object_id,
                ),
            ),
        )

        self.assertEqual(1, len(committed))
        self.assertEqual(
            [blocker.object_id], combat.blockers[attacker.object_id]
        )
        self.assertEqual(attacker.object_id, blocker.blocking)

    def test_duplicate_blocker_assignment_is_rejected_until_multi_block_is_supported(
        self,
    ):
        first_attacker = self.card("A1", "A")
        second_attacker = self.card("A2", "A")
        blocker = self.card("B1", "B")
        combat = CombatState(
            attackers={
                first_attacker.object_id: "B",
                second_attacker.object_id: "B",
            }
        )

        with self.assertRaisesRegex(
            CombatRelationshipStateError,
            "A blocker cannot be committed more than once",
        ):
            commit_block_declaration(
                combat,
                {
                    first_attacker.object_id: first_attacker,
                    second_attacker.object_id: second_attacker,
                    blocker.object_id: blocker,
                },
                controller="B",
                assignments=(
                    BlockDeclarationAssignment(
                        blocker_object_id=blocker.object_id,
                        attacker_object_id=first_attacker.object_id,
                    ),
                    BlockDeclarationAssignment(
                        blocker_object_id=blocker.object_id,
                        attacker_object_id=second_attacker.object_id,
                    ),
                ),
            )

        self.assertEqual({}, combat.blockers)
        self.assertIsNone(blocker.blocking)

    def test_source_departure_makes_resolution_do_nothing(self):
        source = _participant(
            "A1",
            "A",
            _spec(CombatKeywordTriggerKind.BUSHIDO, 2),
        )
        blocker = _participant("B1", "B")
        event = build_block_transition(
            _Query(
                (source, blocker),
                {source.object_id: (blocker.object_id,)},
            )
        )
        assert event is not None
        occurrence = next(
            value
            for value in derive_block_keyword_trigger_occurrences(event)
            if value.kind is CombatKeywordTriggerKind.BUSHIDO
        )
        card = self.card("A1", "A")
        card.logical_object_id = "logical:returned-A1"
        host = SimpleNamespace(
            state=SimpleNamespace(
                cards={card.object_id: card},
                continuous_effects=[],
            ),
            _refs=iter(("CE1",)),
            _timestamps=iter((1,)),
            logs=[],
        )
        host._next_ref = lambda _prefix: next(host._refs)
        host._next_zone_timestamp = lambda: next(host._timestamps)
        host._effective_card_data = lambda _card: {}
        host._type_parts = lambda _line: (set(), set(), set())
        host._log = lambda *args, **kwargs: host.logs.append((args, kwargs))

        applied = resolve_block_keyword_trigger(
            host, occurrence, stack_ref="S1"
        )

        self.assertFalse(applied)
        self.assertEqual([], host.state.continuous_effects)
        self.assertFalse(host.logs[-1][0][3]["applied"])

    def test_block_keyword_resolution_uses_canonical_eot_continuous_effect(self):
        source = _participant(
            "A1",
            "A",
            _spec(CombatKeywordTriggerKind.BUSHIDO, 2),
        )
        blocker = _participant("B1", "B")
        event = build_block_transition(
            _Query(
                (source, blocker),
                {source.object_id: (blocker.object_id,)},
            )
        )
        assert event is not None
        occurrence = next(
            value
            for value in derive_block_keyword_trigger_occurrences(event)
            if value.kind is CombatKeywordTriggerKind.BUSHIDO
        )
        card = self.card("A1", "A")
        host = SimpleNamespace(
            state=SimpleNamespace(
                cards={card.object_id: card},
                continuous_effects=[],
            ),
            _refs=iter(("CE1",)),
            _timestamps=iter((17,)),
            logs=[],
        )
        host._next_ref = lambda _prefix: next(host._refs)
        host._next_zone_timestamp = lambda: next(host._timestamps)
        host._effective_card_data = lambda _card: {}
        host._type_parts = lambda _line: (set(), set(), set())
        host._log = lambda *args, **kwargs: host.logs.append((args, kwargs))

        applied = resolve_block_keyword_trigger(
            host, occurrence, stack_ref="S1"
        )

        self.assertTrue(applied)
        self.assertEqual(1, len(host.state.continuous_effects))
        effect = host.state.continuous_effects[0]
        self.assertEqual("7c", effect.sublayer)
        self.assertEqual(
            ("modify_power_toughness", [2, 2]),
            (effect.operations[0].op, list(effect.operations[0].value)),
        )
        self.assertEqual(
            card.logical_object_id,
            effect.locked_objects[0].logical_object_id,
        )
        self.assertTrue(host.logs[-1][0][3]["applied"])

    def test_flanking_uses_source_snapshot_after_source_leaves(self):
        source = _participant(
            "A1",
            "A",
            _spec(CombatKeywordTriggerKind.FLANKING),
        )
        blocker = _participant("B1", "B")
        event = build_block_transition(
            _Query(
                (source, blocker),
                {source.object_id: (blocker.object_id,)},
            )
        )
        assert event is not None
        occurrence = next(
            value
            for value in derive_block_keyword_trigger_occurrences(event)
            if value.kind is CombatKeywordTriggerKind.FLANKING
        )
        affected = self.card("B1", "B")
        host = SimpleNamespace(
            state=SimpleNamespace(
                cards={affected.object_id: affected},
                continuous_effects=[],
            ),
            _refs=iter(("CE1",)),
            _timestamps=iter((19,)),
            logs=[],
        )
        host._next_ref = lambda _prefix: next(host._refs)
        host._next_zone_timestamp = lambda: next(host._timestamps)
        host._effective_card_data = lambda _card: {}
        host._type_parts = lambda _line: (set(), set(), set())
        host._log = lambda *args, **kwargs: host.logs.append((args, kwargs))

        applied = resolve_block_keyword_trigger(
            host, occurrence, stack_ref="S1"
        )

        self.assertTrue(applied)
        operation = host.state.continuous_effects[0].operations[0]
        self.assertEqual([-1, -1], list(operation.value))


if __name__ == "__main__":
    unittest.main()
