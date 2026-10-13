from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import keep_all, load_assets, make_session, pass_current
from quorune.ability_fragments import (
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
    ability_fragment_to_dict,
)
from quorune.errors import StateInvariantError
from quorune.model import CombatState
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)


class AttackKeywordTriggerIntegrationTests(unittest.TestCase):
    def test_phased_out_trigger_source_is_absent_from_real_attack_transition(self):
        session=self.make_session(388050);engine=session.engine
        attacker=self.permanent(engine,'Active Attacker')
        absent=self.permanent(engine,'Phased Exalted Source',CombatKeywordTriggerKind.EXALTED,creature=False)
        absent.phased_out=True
        engine._issue_attackers();session.initial_checkpoint=checkpoint_envelope(engine.state)
        session.commands.clear();session.decisions.clear()
        result=session.act('pilot:A',{'action_id':'attack','attackers':{attacker.ref:'B'}})
        self.assertTrue(result.ok,result.summary)
        self.assertFalse(any(item.source_object_id==absent.object_id for item in engine.state.stack))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'phased-attack-source';session.save(path)
            replay=replay_record(path,self.db,verify=True)
        self.assertTrue(replay['ok'],replay)
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def fragment(kind: CombatKeywordTriggerKind) -> dict:
        return ability_fragment_to_dict(
            CombatKeywordTriggerSpec(kind=kind, amount=1)
        )

    def permanent(
        self,
        engine,
        name: str,
        *kinds: CombatKeywordTriggerKind,
        creature: bool = True,
    ):
        ref = engine.create_token(
            "A",
            name=name,
            characteristics={
                "type_line": (
                    "Token Creature — Test" if creature else "Token Artifact"
                ),
                "power": "2" if creature else None,
                "toughness": "2" if creature else None,
                "ability_fragments": [self.fragment(kind) for kind in kinds],
            },
            temporary_keywords=("Haste",) if creature else (),
        )[0]
        return engine._resolve_object("A", ref, zones={"battlefield"})

    @staticmethod
    def declare(session, attacks: dict[str, str]):
        session.engine._issue_attackers()
        return session.act("pilot:A", {"a": "attack", "atk": attacks})

    def test_attack_keywords_share_one_completed_declaration_batch(self):
        session = self.make_session(702_121_001)
        engine = session.engine
        battle_cry = self.permanent(
            engine,
            "Battle Cry attacker",
            CombatKeywordTriggerKind.BATTLE_CRY,
        )
        melee = self.permanent(
            engine,
            "Melee attacker",
            CombatKeywordTriggerKind.MELEE,
            CombatKeywordTriggerKind.MELEE,
        )
        ordinary = self.permanent(engine, "Ordinary attacker")

        result = self.declare(
            session,
            {
                battle_cry.ref: "B",
                melee.ref: "C",
                ordinary.ref: "C",
            },
        )

        self.assertTrue(result.ok, result.summary)
        decision = session.state.pending_decision
        self.assertIsNotNone(decision)
        self.assertEqual("trigger.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        self.assertEqual(1, len(session.state.pending_trigger_batches))
        batch = session.state.pending_trigger_batches[0]
        self.assertEqual(3, len(batch.items))
        self.assertEqual(
            1,
            sum("Battle Cry" in item["label"] for item in batch.items),
        )
        self.assertEqual(
            2,
            sum("Melee" in item["label"] for item in batch.items),
        )
        transition_ids = {
            item["context"]["attack_keyword_trigger"]["transition_id"]
            for item in batch.items
        }
        self.assertEqual(1, len(transition_ids))
        melee_amounts = {
            item["context"]["attack_keyword_trigger"]["amount"]
            for item in batch.items
            if "Melee" in item["label"]
        }
        self.assertEqual({2}, melee_amounts)

    def test_exalted_from_noncreature_source_resolves_on_lone_attacker(self):
        session = self.make_session(702_083_001, players=2)
        engine = session.engine
        attacker = self.permanent(engine, "Lone attacker")
        exalted_source = self.permanent(
            engine,
            "Exalted source",
            CombatKeywordTriggerKind.EXALTED,
            creature=False,
        )

        result = self.declare(session, {attacker.ref: "B"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, len(session.state.stack))
        self.assertIn("Exalted", session.state.stack[-1].label)
        engine.move_card(exalted_source.object_id, "graveyard")

        for _seat in engine.active_seats:
            pass_current(session)

        current = engine._effective_card_data(attacker)
        self.assertEqual("3", current["power"])
        self.assertEqual("3", current["toughness"])

    def test_multiple_attackers_do_not_create_exalted_trigger(self):
        session = self.make_session(702_083_002, players=2)
        engine = session.engine
        first = self.permanent(engine, "First attacker")
        second = self.permanent(engine, "Second attacker")
        self.permanent(
            engine,
            "Exalted source",
            CombatKeywordTriggerKind.EXALTED,
            creature=False,
        )

        result = self.declare(
            session,
            {first.ref: "B", second.ref: "B"},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(
            any("Exalted" in item.label for item in session.state.stack)
        )
        self.assertFalse(session.state.pending_trigger_batches)

    def test_attack_keyword_resolution_uses_source_lki_and_identity_pinned_layer(self):
        session = self.make_session(702_121_004)
        engine = session.engine
        battle_cry = self.permanent(
            engine,
            "Departing Battle Cry attacker",
            CombatKeywordTriggerKind.BATTLE_CRY,
        )
        melee = self.permanent(
            engine,
            "Melee attacker",
            CombatKeywordTriggerKind.MELEE,
        )
        ordinary = self.permanent(engine, "Other attacker")

        result = self.declare(
            session,
            {
                battle_cry.ref: "B",
                melee.ref: "C",
                ordinary.ref: "C",
            },
        )
        self.assertTrue(result.ok, result.summary)
        decision = session.state.pending_decision
        trigger_refs = [
            row["id"]
            for row in decision.payload_by_actor["A"]["triggers"]
        ]
        ordered = session.act(
            "pilot:A",
            {"a": "order", "triggers": trigger_refs},
        )
        self.assertTrue(ordered.ok, ordered.summary)
        self.assertEqual(2, len(session.state.stack))

        engine.move_card(battle_cry.object_id, "graveyard")
        while session.state.stack:
            for _seat in engine.active_seats:
                pass_current(session)

        melee_current = engine._effective_card_data(melee)
        ordinary_current = engine._effective_card_data(ordinary)
        self.assertEqual("5", melee_current["power"])
        self.assertEqual("4", melee_current["toughness"])
        self.assertEqual("3", ordinary_current["power"])
        self.assertEqual("2", ordinary_current["toughness"])

    def test_attack_transition_is_public_without_hidden_cards(self):
        session = self.make_session(702_091_001)
        attacker = self.permanent(
            session.engine,
            "Public Battle Cry attacker",
            CombatKeywordTriggerKind.BATTLE_CRY,
        )
        self.assertTrue(self.declare(session, {attacker.ref: "B"}).ok)

        for seat in session.state.players:
            packet = session.packet(f"pilot:{seat}", full=True)
            event = next(
                row
                for row in packet["events"]
                if row["c"] == "combat.attack_transition"
            )
            self.assertIn("represented trigger", event["s"])
            for other in session.state.players:
                if other == seat:
                    continue
                self.assertNotIn(
                    "hand", packet["state"]["players"][other]
                )

    def test_attack_transition_replays_exactly(self):
        session = self.make_session(702_121_002)
        melee = self.permanent(
            session.engine,
            "Replay Melee attacker",
            CombatKeywordTriggerKind.MELEE,
        )
        session.engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        result = session.act(
            "pilot:A",
            {"a": "attack", "atk": {melee.ref: "B"}},
        )
        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(session.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "attack-keyword-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(1, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_malformed_transition_rolls_back_attack_declaration(self):
        session = self.make_session(702_083_003, players=2)
        attacker = self.permanent(session.engine, "Rollback attacker")
        session.engine._issue_attackers()
        before = authoritative_state_hash(session.state)

        with patch(
            (
                "quorune.attack_transition_engine_adapter."
                "attack_transition_stack_items"
            ),
            side_effect=StateInvariantError("malformed attack transition"),
        ):
            with self.assertRaisesRegex(
                StateInvariantError, "malformed attack transition"
            ):
                session.act(
                    "pilot:A",
                    {"a": "attack", "atk": {attacker.ref: "B"}},
                )

        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertFalse(session.state.combat.attackers)
        self.assertEqual(["A"], session.state.pending_decision.actors)


if __name__ == "__main__":
    unittest.main()
