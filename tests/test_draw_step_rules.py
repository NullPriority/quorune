from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from common import (
    keep_all,
    load_assets,
    make_session,
    pass_current,
    set_fixture_turn,
)
from quorune.engine import TURN_STEPS
from quorune.record import replay_record


class DrawStepRuleTests(unittest.TestCase):
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
        return session

    @staticmethod
    def prepare_draw_step(
        session,
        *,
        active: str = "A",
        turn_sequence: int = 2,
    ) -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.active_player = active
        set_fixture_turn(engine, turn_sequence)
        engine.state.phase_index = TURN_STEPS.index(
            ("beginning", "draw")
        )

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and card.is_card_object
            and card.printed_name == name
        )

    def test_contract_traces_every_cr_504_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "draw-step.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"504", "504.1", "504.2"},
            set(contract["rule_references"]),
        )

    def test_turn_draw_is_stackless_and_precedes_active_priority(self):
        session = self.make_session(50401, players=2)
        engine = session.engine
        self.prepare_draw_step(session)
        player = engine.state.players["A"]
        top_object_id = player.zones["library"][-1]
        hand_before = len(player.zones["hand"])
        event_before = engine.state.event_sequence

        engine._enter_step()

        self.assertEqual(hand_before + 1, len(player.zones["hand"]))
        self.assertEqual("hand", engine.state.cards[top_object_id].zone)
        self.assertFalse(engine.state.stack)
        self.assertEqual("A", engine.state.priority_player)
        self.assertEqual(
            "turn-based draw",
            player.draw_history[-1]["reason"],
        )
        events = [
            event.code
            for event in engine.state.events
            if event.event_id > event_before
        ]
        self.assertIn("card.draw", events)
        self.assertNotIn("stack.cast", events)
        self.assertNotIn("stack.trigger", events)

    def test_draw_step_triggers_wait_until_after_the_turn_draw(self):
        session = self.make_session(50402, players=4)
        engine = session.engine
        sylvan = self.card(engine, "B", "Sylvan Library")
        engine.move_card(
            sylvan.object_id,
            "battlefield",
            controller="B",
            log=False,
            semantic_events=False,
        )
        self.prepare_draw_step(session, active="B")
        hand_before = len(engine.state.players["B"].zones["hand"])
        engine.schedule_delayed_trigger(
            controller="B",
            label="CR 504 delayed draw-step trigger",
            event_kind="step.begin",
            condition={
                "phase": "beginning",
                "step": "draw",
                "player": "B",
            },
            stack_template={
                "label": "CR 504 delayed draw-step trigger",
                "context": {"test": "CR 504.2"},
            },
        )
        event_before = engine.state.event_sequence

        engine._enter_step()

        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["B"].zones["hand"]),
        )
        self.assertEqual("trigger.order", engine.state.pending_decision.kind)
        self.assertEqual(["B"], engine.state.pending_decision.actors)
        self.assertIsNone(engine.state.priority_player)
        trigger_refs = [
            item["id"]
            for item in engine.state.pending_decision.payload_by_actor[
                "B"
            ]["triggers"]
        ]
        events = [
            event.code
            for event in engine.state.events
            if event.event_id > event_before
        ]
        self.assertIn("card.draw", events)
        self.assertNotIn("stack.trigger", events)

        result = session.act(
            "pilot:B",
            {
                "action_id": "order",
                "triggers": trigger_refs,
                "reason": "Order the permanent and delayed triggers.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        sylvan_trigger, = (program for program in engine.semantics.programs_for_oracle(sylvan.oracle_id)
                            if program.event == "step.begin")
        self.assertEqual(
            {sylvan_trigger.label, "CR 504 delayed draw-step trigger"},
            {item.label for item in engine.state.stack},
        )
        self.assertEqual("B", engine.state.priority_player)
        draw_event = next(
            event
            for event in engine.state.events
            if event.event_id > event_before
            and event.code == "card.draw"
        )
        trigger_events = [
            event
            for event in engine.state.events
            if event.event_id > event_before
            and event.code == "stack.trigger"
        ]
        self.assertEqual(2, len(trigger_events))
        self.assertTrue(
            all(
                draw_event.event_id < event.event_id
                for event in trigger_events
            )
        )

    def test_trusted_draw_replacement_finishes_before_priority(self):
        session = self.make_session(50403, players=2)
        engine = session.engine
        loam = self.card(engine, "B", "Life from the Loam")
        engine.move_card(
            loam.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )
        self.prepare_draw_step(session, active="B")
        hand_before = len(engine.state.players["B"].zones["hand"])

        engine._enter_step()

        self.assertEqual(
            "draw.replacement",
            engine.state.pending_decision.kind,
        )
        self.assertEqual(["B"], engine.state.pending_decision.actors)
        self.assertEqual(
            hand_before,
            len(engine.state.players["B"].zones["hand"]),
        )
        self.assertIsNone(engine.state.priority_player)

        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "choice": "draw",
                "reason": "Take the ordinary turn-based draw.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["B"].zones["hand"]),
        )
        self.assertEqual("B", engine.state.priority_player)
        self.assertEqual("priority", engine.state.pending_decision.kind)

    def test_draw_replacement_choice_is_visible_only_to_affected_seat(self):
        session = self.make_session(504031, players=4)
        engine = session.engine
        loam = self.card(engine, "B", "Life from the Loam")
        engine.move_card(
            loam.object_id,
            "graveyard",
            log=False,
            semantic_events=False,
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None

        engine._begin_draw_sequence(
            "B",
            1,
            reason="private replacement fixture",
        )

        affected = session.packet("pilot:B", full=True)
        opponent = session.packet("pilot:A", full=True)
        self.assertEqual("draw.replacement", affected["decision"]["kind"])
        self.assertIn(
            f"Dredge 3 — {loam.ref}",
            [
                option["label"]
                for option in affected["decision"]["ctx"]["options"]
            ],
        )
        self.assertIsNone(opponent["decision"])
        self.assertNotIn("hand", opponent["state"]["players"]["B"])

    def test_empty_library_loss_is_checked_before_draw_step_priority(self):
        session = self.make_session(50404, players=4)
        engine = session.engine
        self.prepare_draw_step(session)
        for object_id in list(
            engine.state.players["A"].zones["library"]
        ):
            engine.move_card(
                object_id,
                "exile",
                log=False,
                semantic_events=False,
            )
        engine.schedule_delayed_trigger(
            controller="B",
            label="CR 504 post-draw loss ordering witness",
            event_kind="step.begin",
            condition={
                "phase": "beginning",
                "step": "draw",
                "player": "A",
            },
            stack_template={
                "label": "CR 504 post-draw loss ordering witness",
            },
        )
        event_before = engine.state.event_sequence

        engine._enter_step()

        self.assertFalse(engine.state.players["A"].in_game)
        self.assertTrue(engine.state.players["A"].attempted_empty_draw)
        self.assertEqual("B", engine.state.priority_player)
        self.assertEqual(
            ["CR 504 post-draw loss ordering witness"],
            [item.label for item in engine.state.stack],
        )
        events = [
            event
            for event in engine.state.events
            if event.event_id > event_before
            and event.code in {"player.eliminated", "stack.trigger"}
        ]
        self.assertEqual(
            ["player.eliminated", "stack.trigger"],
            [event.code for event in events],
        )

    def test_first_turn_profile_modifier_and_exact_replay(self):
        duel = self.make_session(50405, players=2)
        duel_hand = len(duel.state.players["A"].zones["hand"])
        for _ in range(2):
            pass_current(duel)
        self.assertEqual(("beginning", "draw"), (duel.state.phase, duel.state.step))
        self.assertEqual(duel_hand, len(duel.state.players["A"].zones["hand"]))

        multiplayer = self.make_session(50406, players=4)
        multiplayer_hand = len(
            multiplayer.state.players["A"].zones["hand"]
        )
        for _ in range(4):
            pass_current(multiplayer)
        self.assertEqual(
            ("beginning", "draw"),
            (multiplayer.state.phase, multiplayer.state.step),
        )
        self.assertEqual(
            multiplayer_hand + 1,
            len(multiplayer.state.players["A"].zones["hand"]),
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "draw-step"
            multiplayer.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(len(multiplayer.commands), replay["commands"])


if __name__ == "__main__":
    unittest.main()
