from __future__ import annotations

from dataclasses import replace
import unittest

from quorune.combat_damage_assignment import (
    build_combat_damage_assignment_proposal,
)
from quorune.combat_damage_snapshot import (
    build_combat_damage_snapshot,
    CombatDamageParticipant,
    CombatDamageRecipient,
    CombatDamageSnapshot,
    CombatDamageSnapshotError,
)
from quorune.combat_relationship_state import (
    remove_combat_relationships,
)
from quorune.model import CombatState


class _Query:
    def __init__(self) -> None:
        self.participants = {
            "attacker-id": CombatDamageParticipant(
                object_id="attacker-id",
                logical_object_id="attacker-id@4",
                reference="A21",
                controller="A",
                power=5,
                toughness=5,
                marked_damage=0,
                keywords=frozenset({"Trample"}),
                assigns_damage=True,
            ),
            "blocker-two-id": CombatDamageParticipant(
                object_id="blocker-two-id",
                logical_object_id="blocker-two-id@2",
                reference="B09",
                controller="B",
                power=1,
                toughness=2,
                marked_damage=0,
                keywords=frozenset(),
                assigns_damage=True,
            ),
            "blocker-one-id": CombatDamageParticipant(
                object_id="blocker-one-id",
                logical_object_id="blocker-one-id@8",
                reference="B03",
                controller="B",
                power=2,
                toughness=3,
                marked_damage=1,
                keywords=frozenset(),
                assigns_damage=True,
            ),
        }
        self.participant_ids = (
            "blocker-two-id",
            "attacker-id",
            "blocker-one-id",
        )
        self.attacker_ids = ("attacker-id",)
        self.blocker_ids = ("blocker-two-id", "blocker-one-id")

    def damage_step_identity(self) -> str:
        return "combat-damage:9:0:0"

    def damage_step_index(self) -> int:
        return 0

    def first_strike_step(self) -> bool:
        return False

    def active_player(self) -> str:
        return "A"

    def participant_object_ids(self):
        return self.participant_ids

    def participant(self, object_id: str):
        return self.participants[object_id]

    def attacker_object_ids(self):
        return self.attacker_ids

    def attack_recipient(self, attacker_object_id: str):
        return CombatDamageRecipient(
            reference="B",
            logical_object_id="player:B",
            controller="B",
            kind="player",
            legal=True,
        )

    def blocker_object_ids(self, attacker_object_id: str):
        return self.blocker_ids

    def was_blocked(self, attacker_object_id: str) -> bool:
        return True


class CombatDamageSnapshotTests(unittest.TestCase):
    def test_snapshot_and_proposal_have_one_canonical_relationship_order(self):
        snapshot = build_combat_damage_snapshot(_Query())

        self.assertEqual(
            ("A21", "B03", "B09"),
            tuple(value.reference for value in snapshot.participants),
        )
        proposal = build_combat_damage_assignment_proposal(
            seat="A", snapshot=snapshot
        )
        self.assertEqual(
            ["B03", "B09", "B"],
            proposal.projected_options()["A21"]["targets"],
        )

        submitted = [
            {"source": "A21", "target": "B", "amount": 0},
            {"source": "A21", "target": "B09", "amount": 2},
            {"source": "A21", "target": "B03", "amount": 3},
        ]
        canonical = proposal.validate(submitted)
        permuted = proposal.validate(tuple(reversed(submitted)))

        self.assertEqual(canonical, permuted)
        self.assertEqual(
            [
                {"source": "A21", "target": "B03", "amount": 3},
                {"source": "A21", "target": "B09", "amount": 2},
            ],
            [value.to_dict() for value in canonical],
        )
        reordered = _Query()
        reordered.participant_ids = tuple(reversed(reordered.participant_ids))
        reordered.blocker_ids = tuple(reversed(reordered.blocker_ids))
        equivalent = build_combat_damage_assignment_proposal(
            seat="A",
            snapshot=build_combat_damage_snapshot(reordered),
        )
        reconstructed = type(proposal)(
            damage_step_id=proposal.damage_step_id,
            actor=proposal.actor,
            sources=proposal.sources,
            attacking_sources=proposal.attacking_sources,
            deathtouch_sources=proposal.deathtouch_sources,
            trample_sources=proposal.trample_sources,
        )
        changed = replace(
            proposal,
            sources=(replace(proposal.sources[0], power=6),),
        )
        self.assertEqual(proposal.proposal_id, equivalent.proposal_id)
        self.assertEqual(proposal.proposal_id, reconstructed.proposal_id)
        self.assertNotEqual(proposal.proposal_id, changed.proposal_id)

    def test_snapshot_deeply_freezes_query_values(self):
        query = _Query()
        snapshot = build_combat_damage_snapshot(query)
        query.participant_ids = ()
        query.blocker_ids = ()
        query.participants["attacker-id"] = replace(
            query.participants["attacker-id"], power=99
        )

        self.assertEqual(5, snapshot.participants[0].power)
        self.assertEqual(2, len(snapshot.blocks))

    def test_malformed_internal_relationships_fail_before_projection(self):
        cases = []
        missing_blocker = _Query()
        missing_blocker.blocker_ids = ("missing",)
        cases.append(missing_blocker)
        duplicate_source = _Query()
        duplicate_source.attacker_ids = ("attacker-id", "attacker-id")
        cases.append(duplicate_source)
        wrong_controller = _Query()
        wrong_controller.participants["attacker-id"] = replace(
            wrong_controller.participants["attacker-id"], controller="B"
        )
        cases.append(wrong_controller)

        for query in cases:
            with self.subTest(query=query):
                with self.assertRaises(CombatDamageSnapshotError):
                    build_combat_damage_snapshot(query)

    def test_exact_numeric_and_identity_invariants_reject_booleans(self):
        with self.assertRaises(CombatDamageSnapshotError):
            CombatDamageParticipant(
                object_id="source",
                logical_object_id="source@1",
                reference="A01",
                controller="A",
                power=True,
                toughness=1,
                marked_damage=0,
                keywords=frozenset(),
                assigns_damage=True,
            )
        with self.assertRaises(CombatDamageSnapshotError):
            CombatDamageSnapshot(
                damage_step_id="step",
                damage_step_index=True,
                first_strike_step=False,
                active_player="A",
                participants=(),
                attacks=(),
                blocks=(),
                was_blocked=frozenset(),
            )
        with self.assertRaisesRegex(
            CombatDamageSnapshotError,
            "collection of keywords",
        ):
            CombatDamageParticipant(
                object_id="source",
                logical_object_id="source@1",
                reference="A01",
                controller="A",
                power=1,
                toughness=1,
                marked_damage=0,
                keywords="trample",
                assigns_damage=True,
            )

    def test_historical_empty_sequence_identity_is_omitted(self):
        serialized = CombatState().to_dict()

        self.assertNotIn("damage_sequence_id", serialized)
        self.assertEqual(serialized, CombatState.from_dict(serialized).to_dict())

    def test_relationship_owner_preserves_blockers_of_removed_attacker(self):
        combat = CombatState(
            attackers={"attacker": "B"},
            blockers={"attacker": ["first", "second"]},
            attack_target_context={
                "attacker": {
                    "target": "B",
                    "kind": "player",
                    "defending_player": "B",
                }
            },
        )

        removal = remove_combat_relationships(combat, "attacker")

        self.assertTrue(removal.was_attacker)
        self.assertEqual({}, combat.attackers)
        self.assertEqual(
            {"attacker": ["first", "second"]},
            combat.blockers,
        )
        self.assertEqual({}, combat.attack_target_context)

    def test_detached_blocker_ids_round_trip_and_end_with_their_combat(self):
        current = CombatState(detached_blocker_ids=["first", "second"])
        payload = current.to_dict()
        restored = CombatState.from_dict(payload)
        self.assertEqual(["first", "second"], list(restored.blocker_object_ids()))
        self.assertTrue(remove_combat_relationships(restored, "first").removed_as_blocker)
        self.assertEqual(["second"], restored.detached_blocker_ids)
        self.assertEqual([], restored.fresh().detached_blocker_ids)
        payload.pop("detached_blocker_ids")
        old = CombatState.from_dict(payload)
        self.assertEqual(payload, old.fresh().to_dict())
        for malformed in (None, [True], ["second", "first"], ["first", "first"]):
            with self.subTest(value=malformed), self.assertRaises(ValueError):
                CombatState.from_dict({**payload, "detached_blocker_ids": malformed})


if __name__ == "__main__":
    unittest.main()
