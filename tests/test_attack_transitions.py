from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import unittest

from quorune.ability_fragments import (
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
)
from quorune.attack_transition_model import (
    AttackKeywordTriggerOccurrence,
    AttackRecipient,
    AttackRecipientKind,
    AttackTransitionError,
    AttackTransitionParticipant,
    build_attack_transition,
    derive_attack_keyword_trigger_occurrences,
)
from quorune.combat_relationship_state import (
    AttackDeclarationAssignment,
    CombatRelationshipStateError,
    commit_attack_declaration,
)
from quorune.model import CombatState


def _spec(kind: CombatKeywordTriggerKind) -> CombatKeywordTriggerSpec:
    return CombatKeywordTriggerSpec(kind=kind, amount=1)


def _participant(
    object_id: str,
    reference: str,
    *specs: CombatKeywordTriggerSpec,
    creature: bool = True,
) -> AttackTransitionParticipant:
    return AttackTransitionParticipant(
        object_id=object_id,
        logical_object_id=f"logical:{object_id}",
        reference=reference,
        controller="A",
        is_creature=creature,
        trigger_specs=specs,
    )


class _Query:
    def __init__(self) -> None:
        self.attackers = ("attacker-a",)
        self.sources = ("attacker-a",)
        self.participants = {
            "attacker-a": _participant("attacker-a", "A01"),
        }
        self.recipients = {
            "attacker-a": AttackRecipient(
                AttackRecipientKind.PLAYER,
                "B",
                "B",
            )
        }

    def turn_sequence(self) -> int:
        return 3

    def priority_epoch(self) -> int:
        return 11

    def active_player(self) -> str:
        return "A"

    def attacker_object_ids(self):
        return self.attackers

    def trigger_source_object_ids(self):
        return self.sources

    def participant(self, object_id: str):
        return self.participants[object_id]

    def recipient(self, attacker_object_id: str):
        return self.recipients[attacker_object_id]


class AttackTransitionModelTests(unittest.TestCase):
    def test_attack_relationship_batch_rejects_before_mutation(self):
        combat = CombatState()
        attacker = SimpleNamespace(
            zone="battlefield",
            controller="A",
            phased_out=False,
            attacking=None,
        )
        assignments = (
            AttackDeclarationAssignment(
                attacker_object_id="attacker-a",
                target="B",
                target_kind="player",
                defending_player="B",
            ),
            AttackDeclarationAssignment(
                attacker_object_id="missing-attacker",
                target="C",
                target_kind="player",
                defending_player="C",
            ),
        )

        with self.assertRaises(CombatRelationshipStateError):
            commit_attack_declaration(
                combat,
                {"attacker-a": attacker},
                controller="A",
                assignments=assignments,
            )

        self.assertIsNone(attacker.attacking)
        self.assertEqual({}, combat.attackers)
        self.assertEqual({}, combat.attack_target_context)

    def test_exalted_triggers_once_per_instance_only_for_attack_alone(self):
        query = _Query()
        query.sources = ("attacker-a", "exalted-land")
        query.participants["attacker-a"] = _participant(
            "attacker-a",
            "A01",
            _spec(CombatKeywordTriggerKind.EXALTED),
        )
        query.participants["exalted-land"] = _participant(
            "exalted-land",
            "A02",
            _spec(CombatKeywordTriggerKind.EXALTED),
            _spec(CombatKeywordTriggerKind.EXALTED),
            creature=False,
        )
        event = build_attack_transition(query)
        self.assertIsNotNone(event)
        occurrences = derive_attack_keyword_trigger_occurrences(event)
        exalted = [
            value
            for value in occurrences
            if value.kind is CombatKeywordTriggerKind.EXALTED
        ]
        self.assertEqual(3, len(exalted))
        self.assertTrue(
            all(value.affected == (query.participants["attacker-a"].identity,) for value in exalted)
        )

        query.attackers = ("attacker-a", "attacker-b")
        query.sources = (*query.sources, "attacker-b")
        query.participants["attacker-b"] = _participant(
            "attacker-b", "A03"
        )
        query.recipients["attacker-b"] = AttackRecipient(
            AttackRecipientKind.PLAYER, "C", "C"
        )
        event = build_attack_transition(query)
        self.assertEqual((), derive_attack_keyword_trigger_occurrences(event))

    def test_battle_cry_triggers_per_instance_and_affects_other_attackers(self):
        query = _Query()
        query.attackers = ("attacker-a", "attacker-b", "attacker-c")
        query.sources = query.attackers
        query.participants = {
            "attacker-a": _participant(
                "attacker-a",
                "A01",
                _spec(CombatKeywordTriggerKind.BATTLE_CRY),
                _spec(CombatKeywordTriggerKind.BATTLE_CRY),
            ),
            "attacker-b": _participant("attacker-b", "A02"),
            "attacker-c": _participant("attacker-c", "A03"),
        }
        query.recipients = {
            object_id: AttackRecipient(
                AttackRecipientKind.PLAYER, "B", "B"
            )
            for object_id in query.attackers
        }
        event = build_attack_transition(query)
        occurrences = derive_attack_keyword_trigger_occurrences(event)
        battle_cry = [
            value
            for value in occurrences
            if value.kind is CombatKeywordTriggerKind.BATTLE_CRY
        ]
        self.assertEqual(2, len(battle_cry))
        self.assertEqual(
            {"attacker-b", "attacker-c"},
            {value.object_id for value in battle_cry[0].affected},
        )
        self.assertNotIn(battle_cry[0].source, battle_cry[0].affected)

    def test_melee_counts_distinct_opponents_attacked_as_players(self):
        query = _Query()
        query.attackers = (
            "melee-a",
            "attacker-b",
            "attacker-c",
            "attacker-d",
        )
        query.sources = query.attackers
        query.participants = {
            "melee-a": _participant(
                "melee-a",
                "A01",
                _spec(CombatKeywordTriggerKind.MELEE),
                _spec(CombatKeywordTriggerKind.MELEE),
            ),
            "attacker-b": _participant("attacker-b", "A02"),
            "attacker-c": _participant("attacker-c", "A03"),
            "attacker-d": _participant("attacker-d", "A04"),
        }
        query.recipients = {
            "melee-a": AttackRecipient(
                AttackRecipientKind.PLAYER, "B", "B"
            ),
            "attacker-b": AttackRecipient(
                AttackRecipientKind.PLAYER, "C", "C"
            ),
            "attacker-c": AttackRecipient(
                AttackRecipientKind.PLAYER, "C", "C"
            ),
            "attacker-d": AttackRecipient(
                AttackRecipientKind.PLANESWALKER,
                "C-walker",
                "C",
                "logical:walker",
            ),
        }
        event = build_attack_transition(query)
        melee = [
            value
            for value in derive_attack_keyword_trigger_occurrences(event)
            if value.kind is CombatKeywordTriggerKind.MELEE
        ]
        self.assertEqual(2, len(melee))
        self.assertEqual([2, 2], [value.amount for value in melee])
        self.assertTrue(all(value.affected == (value.source,) for value in melee))

    def test_melee_still_triggers_for_zero_opponents_attacked_directly(self):
        query = _Query()
        query.participants["attacker-a"] = _participant(
            "attacker-a",
            "A01",
            _spec(CombatKeywordTriggerKind.MELEE),
        )
        query.recipients["attacker-a"] = AttackRecipient(
            AttackRecipientKind.BATTLE,
            "battle-ref",
            "B",
            "logical:battle",
        )
        event = build_attack_transition(query)
        occurrence = derive_attack_keyword_trigger_occurrences(event)[0]
        self.assertEqual(0, occurrence.amount)
        self.assertEqual(0, occurrence.power_delta)

    def test_query_order_does_not_change_event_or_occurrence_identity(self):
        query = _Query()
        query.attackers = ("attacker-b", "attacker-a")
        query.sources = ("anthem", "attacker-a", "attacker-b")
        query.participants = {
            "attacker-a": _participant(
                "attacker-a",
                "A01",
                _spec(CombatKeywordTriggerKind.BATTLE_CRY),
            ),
            "attacker-b": _participant("attacker-b", "A02"),
            "anthem": _participant(
                "anthem",
                "A03",
                _spec(CombatKeywordTriggerKind.EXALTED),
                creature=False,
            ),
        }
        query.recipients["attacker-b"] = AttackRecipient(
            AttackRecipientKind.PLAYER, "C", "C"
        )
        first = build_attack_transition(query)
        query.attackers = tuple(reversed(query.attackers))
        query.sources = tuple(reversed(query.sources))
        second = build_attack_transition(query)
        self.assertEqual(first, second)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(
            derive_attack_keyword_trigger_occurrences(first),
            derive_attack_keyword_trigger_occurrences(second),
        )

    def test_malformed_attack_transition_and_occurrence_fail_closed(self):
        query = _Query()
        query.attackers = ("attacker-a", "attacker-a")
        with self.assertRaises(AttackTransitionError):
            build_attack_transition(query)

        query = _Query()
        query.participants["attacker-a"] = _participant(
            "attacker-a",
            "A01",
            _spec(CombatKeywordTriggerKind.EXALTED),
        )
        occurrence = derive_attack_keyword_trigger_occurrences(
            build_attack_transition(query)
        )[0]
        malformed = deepcopy(occurrence.to_dict())
        malformed["amount"] = True
        with self.assertRaises(AttackTransitionError):
            AttackKeywordTriggerOccurrence.from_dict(malformed)
        malformed = deepcopy(occurrence.to_dict())
        malformed["unknown"] = "closed"
        with self.assertRaises(AttackTransitionError):
            AttackKeywordTriggerOccurrence.from_dict(malformed)

    def test_attack_participant_keywords_round_trip_and_fail_closed(self):
        participant = AttackTransitionParticipant(
            object_id="attacker-a",
            logical_object_id="logical:attacker-a",
            reference="A01",
            controller="A",
            is_creature=True,
            keywords=("Flying", "Defender"),
        )

        self.assertEqual(("defender", "flying"), participant.keywords)
        self.assertEqual(
            participant,
            AttackTransitionParticipant.from_dict(participant.to_dict()),
        )
        malformed = participant.to_dict()
        malformed["keywords"] = "flying"
        with self.assertRaises(AttackTransitionError):
            AttackTransitionParticipant.from_dict(malformed)
        for keywords in ((None,), ("",), ("Flying", "flying"), "flying"):
            with self.subTest(keywords=keywords):
                with self.assertRaises(AttackTransitionError):
                    AttackTransitionParticipant(
                        object_id="attacker-a",
                        logical_object_id="logical:attacker-a",
                        reference="A01",
                        controller="A",
                        is_creature=True,
                        keywords=keywords,
                    )

    def test_bounded_multiplicity_grid_preserves_every_instance(self):
        for exalted_count in range(4):
            for battle_cry_count in range(4):
                for melee_count in range(4):
                    with self.subTest(
                        exalted=exalted_count,
                        battle_cry=battle_cry_count,
                        melee=melee_count,
                    ):
                        query = _Query()
                        query.participants["attacker-a"] = _participant(
                            "attacker-a",
                            "A01",
                            *(
                                [_spec(CombatKeywordTriggerKind.EXALTED)]
                                * exalted_count
                            ),
                            *(
                                [_spec(CombatKeywordTriggerKind.BATTLE_CRY)]
                                * battle_cry_count
                            ),
                            *(
                                [_spec(CombatKeywordTriggerKind.MELEE)]
                                * melee_count
                            ),
                        )
                        occurrences = derive_attack_keyword_trigger_occurrences(
                            build_attack_transition(query)
                        )
                        counts = {
                            kind: sum(value.kind is kind for value in occurrences)
                            for kind in (
                                CombatKeywordTriggerKind.EXALTED,
                                CombatKeywordTriggerKind.BATTLE_CRY,
                                CombatKeywordTriggerKind.MELEE,
                            )
                        }
                        self.assertEqual(exalted_count, counts[CombatKeywordTriggerKind.EXALTED])
                        self.assertEqual(battle_cry_count, counts[CombatKeywordTriggerKind.BATTLE_CRY])
                        self.assertEqual(melee_count, counts[CombatKeywordTriggerKind.MELEE])


if __name__ == "__main__":
    unittest.main()
