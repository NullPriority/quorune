from __future__ import annotations

import unittest

from common import keep_all, load_assets, make_session
from quorune.errors import GameRuleError, StateInvariantError
from quorune.record import authoritative_state_hash
from quorune.turn_priority_model import PriorityGrantPlan, PriorityPassPlan
from quorune.turn_step_owner import TURN_STEPS


class TurnPriorityDecisionOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_engine(self, seed: int):
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
        engine.state.priority_player = None
        engine.state.priority_passes = []
        return engine

    def test_priority_plans_are_closed_immutable_and_canonical(self):
        grant = PriorityGrantPlan("A", 4, cleanup_frame=False)
        self.assertEqual(grant, PriorityGrantPlan.from_dict(grant.to_dict()))
        with self.assertRaisesRegex(ValueError, "invalid shape"):
            PriorityGrantPlan.from_dict({**grant.to_dict(), "extra": True})
        with self.assertRaisesRegex(ValueError, "exact integer"):
            PriorityGrantPlan("A", True)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "nonempty string"):
            PriorityGrantPlan.from_dict(
                {"seat": 1, "priority_epoch": 4, "cleanup_frame": False}
            )

        passed = PriorityPassPlan(
            seat="A",
            passes=("A",),
            next_seat="B",
            round_complete=False,
            stack_waiting=False,
        )
        self.assertEqual(passed, PriorityPassPlan.from_dict(passed.to_dict()))
        with self.assertRaisesRegex(ValueError, "unique"):
            PriorityPassPlan(
                seat="A",
                passes=("A", "A"),
                next_seat="B",
                round_complete=False,
                stack_waiting=False,
            )
        with self.assertRaisesRegex(ValueError, "cannot name a next seat"):
            PriorityPassPlan(
                seat="A",
                passes=("A", "B"),
                next_seat="A",
                round_complete=True,
                stack_waiting=False,
            )
        with self.assertRaisesRegex(ValueError, "nonempty string"):
            PriorityPassPlan.from_dict(
                {
                    "seat": "A",
                    "passes": ["A"],
                    "next_seat": 2,
                    "round_complete": False,
                    "stack_waiting": False,
                }
            )

    def test_engine_facade_commits_through_typed_priority_owner(self):
        engine = self.make_engine(5001001)
        owner = engine.turn_priority
        plan = owner.prepare_priority_grant("A", cleanup_frame=False)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(engine.state.priority_epoch + 1, plan.priority_epoch)

        owner.commit_priority_grant(plan)

        self.assertEqual("A", engine.state.priority_player)
        self.assertEqual([], engine.state.priority_passes)
        self.assertEqual(plan.priority_epoch, engine.state.priority_epoch)
        with self.assertRaisesRegex(StateInvariantError, "stale"):
            owner.commit_priority_grant(plan)

    def test_priority_pass_plan_preserves_turn_order_and_rejects_wrong_actor(self):
        engine = self.make_engine(5001002)
        engine._grant_priority("A")

        with self.assertRaisesRegex(GameRuleError, "does not have priority"):
            engine.turn_priority.prepare_priority_pass("B")
        plan = engine.turn_priority.prepare_priority_pass("A")
        self.assertEqual(("A",), plan.passes)
        self.assertEqual("B", plan.next_seat)
        self.assertFalse(plan.round_complete)

        engine.turn_priority.commit_priority_pass(plan, automatic=True)
        self.assertEqual("B", engine.state.priority_player)
        self.assertEqual(["A"], engine.state.priority_passes)

    def test_yield_epochs_retain_game_record_v3_keys_and_invalidate(self):
        engine = self.make_engine(5001003)
        engine._grant_priority("A")
        engine._set_yield("A", "until_public_change")
        before = engine._yield_change_epoch("stack")

        engine._log(
            "A",
            "stack.cast",
            "A stack object changed the priority window.",
            importance=0,
        )

        self.assertEqual(before + 1, engine._yield_change_epoch("stack"))
        self.assertIn("yield_change:stack", engine.state.ref_counters)
        self.assertEqual("stack", engine._yield_stop_reason("A"))

    def test_owner_mutation_rolls_back_and_remains_bound_to_live_state(self):
        engine = self.make_engine(5001004)
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(GameRuleError, "rollback witness"):
            with engine.transaction():
                engine._grant_priority("A")
                raise GameRuleError("rollback witness")

        self.assertEqual(before, authoritative_state_hash(engine.state))
        engine._grant_priority("A")
        self.assertEqual("A", engine.state.priority_player)

    def test_priority_decision_payload_has_stable_yield_modes(self):
        engine = self.make_engine(5001005)
        engine._grant_priority("A")
        decision = engine._issue_priority(
            "A",
            {
                "actions": [{"id": "pass", "kind": "pass"}],
                "cast": [],
                "lands": [],
                "abilities": [],
                "mana_abilities": [],
            },
        )

        self.assertEqual(
            [
                "none",
                "until_public_change",
                "until_my_turn",
                "auto_if_no_response",
            ],
            decision.payload_by_actor["A"]["yield_modes"],
        )

    def test_turn_owner_selects_extra_turns_lifo_then_normal_order(self):
        engine = self.make_engine(5001006)
        engine.schedule_extra_turn("B", source="first")
        engine.schedule_extra_turn("A", source="second")

        self.assertEqual("A", engine._select_next_turn().player)
        self.assertEqual("B", engine._select_next_turn().player)
        self.assertEqual("B", engine._select_next_turn().player)

    def test_turn_owner_rejects_invalid_step_index_before_mutation(self):
        engine = self.make_engine(5001007)
        engine.state.phase_index = len(TURN_STEPS)
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(StateInvariantError, "turn-step index"):
            engine._enter_step()

        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_turn_owner_step_entry_sets_boundary_before_callback(self):
        engine = self.make_engine(5001008)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.phase_index = TURN_STEPS.index(
            ("precombat_main", "main")
        )
        engine.state.phase = "beginning"
        engine.state.step = "draw"

        engine._enter_step()

        self.assertEqual("precombat_main", engine.state.phase)
        self.assertEqual("main", engine.state.step)
        self.assertTrue(
            any(
                event.code == "step.begin"
                and event.summary.endswith("precombat_main/main.")
                for event in engine.state.events
            )
        )

    def test_departed_active_player_turn_continues_through_step_boundary(self):
        engine = self.make_engine(5001009)
        self.assertEqual("A", engine.state.active_player)
        engine.state.players["A"].in_game = False
        engine.state.phase_index = TURN_STEPS.index(
            ("postcombat_main", "main")
        )

        engine._enter_step()

        self.assertEqual("A", engine.state.active_player)
        self.assertEqual("postcombat_main", engine.state.phase)
        self.assertEqual("main", engine.state.step)
        self.assertEqual("B", engine.state.priority_player)


class ConcessionPriorityRoundRuntimeTests(unittest.TestCase):
    """CR 104.3a/117.4/800.4a: concession interrupts successive passes.

    After the priority holder concedes, the next remaining player receives
    a usable priority window over the changed game. A pass from that player
    cannot collide with their pass before the concession. Pending records
    retain the same controller, targets, principal privacy and exact replay.
    """

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        import tempfile
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "concession-priority.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Concession priority", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 10),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    replay = _bound.BoundEffectProgramRuntimeTests.replay

    def test_actual_last_seat_concession_allows_wrapped_priority_pass_and_replays(self):
        session = self.session(343001)
        source = self.add(session.engine, "Generic Bound Draw Loss", zone="hand")
        action = self.ready(session, source, {"B": 1})
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": ["A"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        hand_before = len(session.state.players["A"].zones["hand"])
        for seat in "ABC":
            self.assertTrue(session.act("pilot:" + seat, {"action_id": "pass"}).ok)
        before = authoritative_state_hash(session.state)
        malformed = session.act("pilot:D", {"action_id": "concede"})
        self.assertFalse(malformed.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        unauthorized = session.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertFalse(unauthorized.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.replay(session, load=True)
        conceded = session.act("pilot:D", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(conceded.ok, conceded.summary)
        self.assertFalse(session.state.players["D"].in_game)
        self.assertEqual("A", session.state.priority_player)
        self.assertEqual(1, len(session.state.stack))
        self.assertEqual("A", session.state.stack[-1].controller)
        self.assertEqual(["A"], session.state.stack[-1].targets)
        for seat in "BCD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        self.replay(session, load=True)
        passed = session.act("pilot:A", {"action_id": "pass"})
        self.assertTrue(passed.ok, passed.summary)
        self.assertEqual(1, len(session.state.stack))
        self.resolve(session)
        self.assertEqual(38, session.state.players["A"].life)
        self.assertEqual(hand_before + 2, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        for seat in "BC":
            self.assertEqual(40, session.state.players[seat].life)
            public = session.packet("pilot:" + seat, full=True)["state"]["players"]["A"]
            self.assertEqual(hand_before + 2, public["hand_n"])
            self.assertNotIn("hand", public)
        self.replay(session, load=True)

    def test_concession_pass_reset_omission_mutant_is_killed(self):
        from unittest.mock import patch
        from quorune.turn_priority_owner import TurnPriorityDecisionOwner

        original = TurnPriorityDecisionOwner.complete_priority

        def omit_reset(owner, decision):
            previous = tuple(owner.state.priority_passes)
            original(owner, decision)
            if decision.responses[decision.actors[0]].get("action") == "concede":
                owner.state.priority_passes = [seat for seat in previous if seat in owner.host.active_seats]

        with patch.object(TurnPriorityDecisionOwner, "complete_priority", new=omit_reset):
            with self.assertRaisesRegex(StateInvariantError, "duplicate seats"):
                self.test_actual_last_seat_concession_allows_wrapped_priority_pass_and_replays()


class CostPaymentDeparturePriorityTests(unittest.TestCase):
    """CR 118.3b/704.5a/800.4a: full-life costs finish before departure."""

    register_fixture_cards_on_add = True
    import test_bound_effect_programs as _bound
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    replay = _bound.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        import json
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        fixture = Path(cls.temporary.name) / "full-life-costs.json"
        cards = []
        for index, (name, type_line, text) in enumerate([
            ("Generic Full Life Spell", "Instant",
             "As an additional cost to cast this spell, pay 40 life.\nDraw a card."),
            ("Generic Full Life Ability", "Artifact", "Pay 40 life: Draw a card."),
            ("Generic Full Life Mana", "Artifact", "Pay 40 life: Add {B}."),
            ("Generic Full Life Land", "Land",
             "As this land enters, you may pay 2 life. If you don't, it enters tapped."),
        ]):
            cards.append({"oracle_id": f"00000000-0000-4000-8000-{7290+index:012d}",
                "name": name, "mana_cost": "{1}", "cmc": 1, "type_line": type_line,
                "oracle_text": text, "colors": [], "color_identity": [], "keywords": [],
                "layout": "normal", "legalities": {"commander": "legal"}})
        fixture.write_text(json.dumps({"schema_version": 1, "cards": cards}), encoding="utf8")
        path = Path(cls.temporary.name) / "full-life-costs.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Life payment priority", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def prepare(self, session, actor="A"):
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.started = True
        engine.state.active_player = actor
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.phase_index = 3
        engine.state.players[actor].mana_pool["C"] = 1
        engine._grant_priority(actor)
        engine.pump()

    def action(self, session, source, actor="A"):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name),
            semantic_registry=session.engine.semantics, capability_registry=self.registry,
            capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding)
        self.assertEqual("capability_closed", binding["trust_basis"])
        actions = session.packet("pilot:"+actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row.get("card") == source.ref
            or row["id"].startswith("activate:"+source.ref+":"))

    def departure_witness(self, name, *, actor="A", successor="B"):
        import json
        session = self.session(729002)
        source = self.add(session.engine, name, seat=actor,
            zone="hand" if name.endswith("Spell") else "battlefield")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand")
        self.prepare(session, actor)
        action = self.action(session, source, actor)
        self.checkpoint(session)
        # The pending offered action survives save/load before the payment.
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:C", {"action_id": action["id"], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:"+actor, {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, session.state.players[actor].life)
        self.assertFalse(session.state.players[actor].in_game)
        self.assertEqual(actor, session.state.active_player)
        self.assertEqual(successor, session.state.priority_player)
        self.assertEqual([], session.state.priority_passes)
        self.assertEqual("outside", session.state.cards[source.object_id].zone)
        self.assertFalse(session.state.stack)
        self.assertEqual(0 if name.endswith("Spell") else 1,
            session.state.players[actor].mana_pool.get("C", 0))
        self.assertEqual(1 if name.endswith("Mana") else 0,
            session.state.players[actor].mana_pool.get("B", 0))
        for viewer in "ABD":
            self.assertNotIn(private.ref, json.dumps(session.packet("pilot:"+viewer, full=True)))
        self.assertIn(private.ref, json.dumps(session.packet("pilot:C", full=True)))
        session = self.replay(session, load=True)
        passed = session.act("pilot:"+successor, {"action_id": "pass"})
        self.assertTrue(passed.ok, passed.summary)
        self.assertEqual([successor], session.state.priority_passes)
        self.replay(session, load=True)

    def test_full_life_spell_departs_then_gives_next_survivor_usable_priority(self):
        self.departure_witness("Generic Full Life Spell")

    def test_full_life_nonmana_activation_removes_departed_stack_ability(self):
        self.departure_witness("Generic Full Life Ability", actor="D", successor="A")

    def test_full_life_mana_activation_gives_next_survivor_priority(self):
        self.departure_witness("Generic Full Life Mana")

    def test_full_life_land_entry_passes_priority_after_owned_land_leaves(self):
        session = self.session(729006)
        source = self.add(session.engine, "Generic Full Life Land", zone="hand")
        session.state.players["A"].life = 2
        self.prepare(session)
        action = self.action(session, source)
        self.checkpoint(session)
        session = self.replay(session, load=True)
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay_life": True})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(session.state.players["A"].in_game)
        self.assertEqual(0, session.state.players["A"].life)
        self.assertEqual("outside", session.state.cards[source.object_id].zone)
        self.assertEqual("B", session.state.priority_player)
        self.assertTrue(session.act("pilot:B", {"action_id": "pass"}).ok)
        self.replay(session, load=True)

    def test_insufficient_life_rejects_but_surviving_payer_retains_priority(self):
        session = self.session(729003)
        source = self.add(session.engine, "Generic Full Life Spell", zone="hand")
        session.state.players["A"].life = 41
        self.prepare(session)
        action = self.action(session, source)
        self.checkpoint(session)
        epoch = session.state.priority_epoch
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(1, session.state.players["A"].life)
        self.assertTrue(session.state.players["A"].in_game)
        self.assertEqual("A", session.state.priority_player)
        self.assertEqual(epoch, session.state.priority_epoch)
        before_draw = len(session.state.players["A"].zones["hand"])
        self.resolve(session)
        self.assertEqual(before_draw+1, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)

        session = self.session(729004)
        source = self.add(session.engine, "Generic Full Life Spell", zone="hand")
        session.state.players["A"].life = 39
        self.prepare(session)
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:A", {"action_id": "cast:"+source.ref, "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.replay(session, load=True)

    def test_restoring_departed_actor_mutant_is_killed_by_fresh_spell(self):
        from unittest.mock import patch
        from quorune.turn_priority_owner import TurnPriorityDecisionOwner

        def restore_departed(owner, seat):
            owner.state.priority_player = seat
            owner.state.priority_passes = []

        with patch.object(TurnPriorityDecisionOwner, "retain_priority_after_action", new=restore_departed):
            method = "test_full_life_spell_departs_then_gives_next_survivor_usable_priority"
            with self.assertRaisesRegex(StateInvariantError, "not in the game"):
                getattr(type(self)(method), method)()

    def test_special_action_stabilization_preserves_departure_choice_and_game_end(self):
        from unittest.mock import patch
        session = self.session(729005)
        self.prepare(session)
        owner = session.engine.turn_priority

        def depart():
            session.engine._eliminate_players(["A"], reason="full-life special-action witness")
            return False

        with patch.object(session.engine, "_stabilize", side_effect=depart):
            owner.complete_special_action("A")
        self.assertEqual("B", session.state.priority_player)
        self.assertEqual([], session.state.priority_passes)
        with patch.object(session.engine, "_stabilize", return_value=True):
            owner.complete_special_action("B")
        self.assertIsNone(session.state.priority_player)
        session.engine._eliminate_players(["B", "C"], reason="game-end witness")
        self.assertTrue(session.state.game_over)
        owner.retain_priority_after_action("D")
        self.assertIsNone(session.state.priority_player)


if __name__ == "__main__":
    unittest.main()
