from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.engine import GameRuleError
from quorune.record import authoritative_state_hash


class CastingSpellRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        return session

    @staticmethod
    def card(session, owner: str, name: str):
        return next(
            card
            for card in session.state.cards.values()
            if card.owner == owner
            and card.is_card_object
            and card.printed_name == name
        )

    def test_contract_traces_every_cr_601_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "casting-spells.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {
                "601",
                "601.1",
                "601.1a",
                "601.2",
                "601.2a",
                "601.2b",
                "601.2c",
                "601.2d",
                "601.2e",
                "601.2f",
                "601.2g",
                "601.2h",
                "601.2i",
                "601.3",
                "601.3a",
                "601.3b",
                "601.3c",
                "601.3d",
                "601.3e",
                "601.3f",
                "601.4",
                "601.5",
                "601.6",
                "601.6a",
                "601.7",
                "601.7a",
                "601.7b",
                "601.8",
            },
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("601")
            },
        )

    def test_mana_abilities_run_after_cost_choice_and_before_payment(self):
        session = self.make_session(60101)
        engine = session.engine
        island = self.card(session, "A", "Island")
        ring = self.card(session, "A", "Sol Ring")
        engine.move_card(
            island.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(ring.object_id, "hand", log=False)
        start_event = engine.state.event_sequence

        engine._cast(
            "A",
            {
                "card": ring.ref,
                "pay": "manual",
                "mana": [
                    {
                        "source": island.ref,
                        "bundle": {"U": 1},
                    }
                ],
                "payment": {"U": 1},
            },
        )

        cast_events = [
            event
            for event in engine.state.events
            if event.event_id > start_event
            and event.code in {"mana.produce", "stack.cast"}
        ]
        self.assertEqual(
            ["mana.produce", "stack.cast"],
            [event.code for event in cast_events],
        )
        self.assertEqual(
            [{"source": island.ref, "bundle": {"U": 1}}],
            cast_events[-1].details["mana_sources"],
        )
        self.assertTrue(island.tapped)
        self.assertEqual("stack", ring.zone)
        self.assertEqual(
            ring.object_id,
            engine.state.stack[0].card_object_id,
        )
        self.assertEqual(
            0,
            sum(engine.state.players["A"].mana_pool.values()),
        )

    def test_cast_trigger_is_queued_above_spell_before_priority_returns(self):
        session = self.make_session(60102)
        engine = session.engine
        sai = self.card(session, "A", "Sai, Master Thopterist")
        ring = self.card(session, "A", "Sol Ring")
        engine.move_card(
            sai.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(ring.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["C"] = 1
        start_event = engine.state.event_sequence

        engine._cast("A", {"card": ring.ref, "pay": "auto"})

        self.assertEqual("stack", ring.zone)
        self.assertEqual(2, len(engine.state.stack))
        self.assertEqual(
            ring.object_id,
            engine.state.stack[0].card_object_id,
        )
        self.assertEqual(
            ('triggered_ability',sai.object_id),
            (engine.state.stack[-1].kind,engine.state.stack[-1].source_object_id),
        )
        relevant_events = [
            event.code
            for event in engine.state.events
            if event.event_id > start_event
            and event.code in {"stack.cast", "stack.trigger"}
        ]
        self.assertEqual(
            ["stack.cast", "stack.trigger"],
            relevant_events,
        )
        self.assertEqual("A", engine.state.priority_player)
        self.assertEqual([], engine.state.priority_passes)

    def test_failed_cast_submission_restores_every_partial_payment(self):
        session = self.make_session(60103)
        engine = session.engine
        island = self.card(session, "A", "Island")
        ring = self.card(session, "A", "Sol Ring")
        engine.move_card(
            island.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(ring.object_id, "hand", log=False)
        decision = engine._issue_priority("A")
        capability = engine.permissions.capability_for("pilot:A")
        self.assertIsNotNone(capability)
        self.assertEqual(decision.decision_id, capability.decision_id)
        before_hash = authoritative_state_hash(engine.state)

        staged_option = {
            "id": "normal",
            "requirements": {
                "GENERIC": 1,
                "W": 0,
                "U": 0,
                "B": 0,
                "R": 0,
                "G": 0,
                "C": 0,
            },
            "selected_additional_costs": [
                {
                    "kind": "discard",
                    "cards": ["missing-card-ref"],
                }
            ],
        }
        with patch.object(
            engine,
            "_cast_cost_options",
            return_value=[staged_option],
        ):
            result = engine.try_submit(
                token=capability.token,
                principal="pilot:A",
                action="cast",
                payload={"card": ring.ref, "pay": "auto"},
            )

        self.assertFalse(result.ok)
        self.assertIn("State was rolled back.", result.warnings)
        self.assertEqual(before_hash, authoritative_state_hash(engine.state))
        restored_island = engine.state.cards[island.object_id]
        restored_ring = engine.state.cards[ring.object_id]
        self.assertFalse(restored_island.tapped)
        self.assertEqual("hand", restored_ring.zone)
        self.assertEqual([], engine.state.stack)
        restored_capability = engine.permissions.capability_for("pilot:A")
        self.assertIsNotNone(restored_capability)
        self.assertFalse(restored_capability.consumed)

    def test_sorcery_timing_rejects_nonactive_cast_without_mutation(self):
        session = self.make_session(60104)
        engine = session.engine
        ring = self.card(session, "A", "Sol Ring")
        engine.move_card(ring.object_id, "hand", log=False)
        engine.state.active_player = "B"
        engine.state.priority_player = "A"
        before_hash = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(
            GameRuleError,
            "Sorcery-speed action requires the active player",
        ):
            engine._cast("A", {"card": ring.ref, "pay": "auto"})

        self.assertEqual(before_hash, authoritative_state_hash(engine.state))
        self.assertEqual("hand", ring.zone)


if __name__ == "__main__":
    unittest.main()
