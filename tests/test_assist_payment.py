from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.casting_payment_keywords import ASSIST_CAPABILITY
from quorune.damage_modifier_state import DamageModifierDuration, PreventionMode
from quorune.damage_prevention_creation import (
    PreventionShieldCreationRequest, PreventionSubjectAllocation,
    commit_prevention_shield_creation, plan_prevention_shield_creation,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.casting.assist_frame import AssistCastFrame
from quorune.turn_history import current_turn_history_events
from scripts.build_test_database import build_fixture_database
import test_blight


class AssistRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = test_blight.FixedBlightRuntimeTests.session
    add = test_blight.FixedBlightRuntimeTests.add
    prepare = test_blight.FixedBlightRuntimeTests.prepare
    checkpoint = test_blight.FixedBlightRuntimeTests.checkpoint
    resolve = test_blight.FixedBlightRuntimeTests.resolve
    replay = test_blight.FixedBlightRuntimeTests.replay
    assert_normal_admission = test_blight.FixedBlightRuntimeTests.assert_normal_admission

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "assist.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "fixed-mana-result-cards.json",
            "counter-replacement-cards.json", "assist-payment-cards.json",
            "fixed-self-cost-surface-cards.json", "state-based-destination-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Assist", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 16)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def _cast_offer(self, session, spell):
        context = session.packet("pilot:A", full=True)["decision"]["ctx"]
        return next(row for row in context["legal"]["actions"] if row.get("card") == spell.ref)

    def _act(self, session, seat, action_id, **fields):
        result = session.act(f"pilot:{seat}", {"action_id": action_id, **fields})
        self.assertTrue(result.ok, result.summary)

    def _plan_mana(self, session, seat, card, color):
        context = session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]
        option = next(row for row in context["legal_actions"]
                      if row.get("assist_step", {}).get("source") == card.ref
                      and row["assist_step"]["bundle"] == {color: 1})
        self._act(session, seat, option["id"])

    def _frame(self, session, stage):
        decision = session.state.pending_decision
        self.assertEqual("cast.assist", decision.kind)
        frame = AssistCastFrame.from_dict(decision.continuation)
        self.assertEqual(stage, frame.stage)
        self.assertEqual([frame.actor], decision.actors)
        return frame

    def test_printed_assist_requires_its_declared_payment_capability(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(row for row in raw["capabilities"] if row["id"] == ASSIST_CAPABILITY)
        cap.update(status="blocked", blockers=["test Assist withdrawal"])
        withdrawn = CapabilityRegistry(raw)
        withdrawn.mark_evidence_verified("0" * 64)
        for name in ("Bring Down", "Out of Bounds", "Charging Binox"):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                exact = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
                self.assertEqual("exact", exact.status, exact.material_residuals)
                self.assertTrue(any(ASSIST_CAPABILITY in node.capability_dependencies for face in exact.faces for node in face.nodes))
                blocked = compile_oracle_card(card, capability_registry=withdrawn, capability_profile="commander_review")
                self.assertNotEqual("exact", blocked.status)

    def test_actual_bring_down_assist_preserves_both_mana_windows_and_replays(self):
        session = self.session(741101)
        engine = session.engine
        spell = self.add(engine, "Bring Down", zone="hand")
        plains = self.add(engine, "Generic Bound Plains")
        reef = self.add(engine, "Shivan Reef", seat="B")
        target = self.add(engine, "Ancient Brontodon", seat="C")
        hidden = self.add(engine, "Generic Bound Growth", seat="D", zone="hand", ref="assist-other-private")
        for card in (spell, plains, reef, target):
            self.assert_normal_admission(session, card)
        for label in ("one", "two"):
            request = PreventionShieldCreationRequest(
                source_id=f"fixture:assist-mana:{label}", controller="B", mode=PreventionMode.AMOUNT,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,
                subjects=(PreventionSubjectAllocation("B", 1),), label=f"Prevent {label}",
            )
            commit_prevention_shield_creation(engine, plan_prevention_shield_creation(engine, request))
        self.prepare(session, mana={"W": 1, "C": 1})
        self.checkpoint(session)
        life = session.state.players["B"].life
        action = self._cast_offer(session, spell)
        option, = action["cost_options"]
        schema = option["choice_schema"]["assist_helper"]
        self.assertTrue(schema["required"])
        self.assertEqual(["B"], schema["legal_values"])
        command = {"action_id": action["id"], "targets": [target.ref], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        for changed in ({}, {"assist_helper": "A"}, {"assist_helper": "C"},
                        {"assist_helper": "B", "_assist_frame": {}}):
            denied = session.act("pilot:A", {**command, **changed})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {**command, "assist_helper": "B"})
        self.assertTrue(accepted.ok, accepted.summary)
        frame = self._frame(session, "helper_mana")
        self.assertEqual(("A", "B"), (frame.caster, frame.helper))
        self.assertEqual("Bring Down", session.packet("pilot:B", full=True)["decision"]["ctx"]["spell"])
        public = session.packet("pilot:C", full=True)
        self.assertIsNone(public["decision"])
        self.assertEqual("Bring Down", public["state"]["assist_cast"]["spell"])
        self.assertNotIn("legal_actions", public["state"]["assist_cast"])
        self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:B", full=True)))
        self.assertFalse(session.state.stack)
        self.assertEqual("hand", session.state.cards[spell.object_id].zone)
        forged = deepcopy(session.state.pending_decision.continuation)
        forged["stage"] = "caster_payment"
        with self.assertRaises(GameRuleError):
            AssistCastFrame.from_dict(forged)
        session = self.replay(session, load=True)
        self._plan_mana(session, "B", reef, "U")
        replacement = self._frame(session, "helper_mana")
        self.assertIsNotNone(replacement.pending)
        self.assertFalse(session.state.cards[reef.object_id].tapped)
        options = session.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        session = self.replay(session, load=True)
        self._act(session, "B", "choose", replacement=options[0]["id"])
        self.assertIsNone(self._frame(session, "helper_mana").pending)
        self._act(session, "B", "assist:finish-mana")
        self._frame(session, "caster_mana")
        self._plan_mana(session, "A", plains, "W")
        self._act(session, "A", "assist:finish-mana")
        self._frame(session, "helper_payment")
        self.assertFalse(session.state.cards[plains.object_id].tapped)
        self.assertFalse(session.state.cards[reef.object_id].tapped)
        self.assertEqual(life, session.state.players["B"].life)
        before = authoritative_state_hash(session.state)
        for seat, amount in (("C", 1), ("B", 2)):
            denied = session.act(f"pilot:{seat}", {"action_id": "assist:contribute", "assist_amount": amount})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        denied = session.act("pilot:B", {
            "action_id": "assist:contribute", "assist_amount": 1, "assist_finish_mana": True,
        })
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self._act(session, "B", "assist:contribute", assist_amount=1)
        self._frame(session, "caster_payment")
        session = self.replay(session, load=True)
        self._act(session, "A", "assist:pay")
        self.assertTrue(session.state.cards[plains.object_id].tapped)
        self.assertTrue(session.state.cards[reef.object_id].tapped)
        self.assertEqual(life, session.state.players["B"].life)
        self.assertEqual(0, sum(session.state.players["B"].mana_pool.values()),
                         "The chosen helper must actually spend mana")
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        stack, = session.state.stack
        self.assertEqual("A", stack.controller)
        self.assertEqual([target.ref], stack.targets)
        self.assertEqual({"W": 2, "U": 1, "C": 1},
                         {color: amount for color, amount in stack.mana_spent_counts.to_dict()["amounts"].items() if amount})
        spenders = current_turn_history_events(session.state.turn_history,
            turn_sequence=session.state.turn_sequence, kind="spell_mana_spent")
        self.assertEqual([("B", 1), ("A", 3)], [(event.actor, event.amount) for event in spenders])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        self.replay(session, load=True)

    def test_actual_bring_down_declined_help_rolls_back_and_solo_stays_legal(self):
        for solo in (False, True):
            with self.subTest(solo=solo):
                session = self.session(741104 + solo)
                engine = session.engine
                spell = self.add(engine, "Bring Down", zone="hand")
                target = self.add(engine, "Ancient Brontodon", seat="C")
                self.assert_normal_admission(session, spell)
                helper_land = self.add(engine, "Shivan Reef", seat="B") if not solo else None
                session.state.players["B"].mana_pool["C"] = 2
                self.prepare(session, mana={"W": 1, "C": 3 if solo else 0})
                self.checkpoint(session)
                action = self._cast_offer(session, spell)
                command = {"targets": [target.ref], "pay": "auto"}
                if solo:
                    self._act(session, "A", action["id"], **command)
                    self.assertEqual("priority", session.state.pending_decision.kind)
                    self.assertIsNone(self.resolve(session))
                    self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
                else:
                    self._act(session, "A", action["id"], **command, assist_helper="B")
                    self._plan_mana(session, "B", helper_land, "C")
                    self._act(session, "B", "assist:finish-mana")
                    self._act(session, "A", "assist:finish-mana")
                    self._act(session, "B", "assist:contribute", assist_amount=0)
                    self.assertEqual("priority", session.state.pending_decision.kind)
                    self.assertEqual(["pilot:A"], session.pending_principals())
                    self.assertEqual("hand", session.state.cards[spell.object_id].zone)
                    self.assertEqual("battlefield", session.state.cards[target.object_id].zone)
                    self.assertFalse(session.state.stack)
                    self.assertFalse(session.state.cards[helper_land.object_id].tapped)
                    self.assertEqual((1, 2), (sum(session.state.players["A"].mana_pool.values()),
                                             sum(session.state.players["B"].mana_pool.values())))
                self.replay(session, load=True)

    def test_native_assist_detects_unpaid_contribution(self):
        from quorune.rules.casting import assist_payment

        original = assist_payment.commit_assist_cast_payment

        def omit_helper_charge(host, frame, response, requirements, spend_context):
            spent = original(host, frame, response, requirements, spend_context)
            host._add_mana_to_pool(frame.helper, spent[0])
            return spent

        with patch.object(assist_payment, "commit_assist_cast_payment", side_effect=omit_helper_charge):
            with self.assertRaisesRegex(AssertionError, "helper must actually spend mana"):
                self.test_actual_bring_down_assist_preserves_both_mana_windows_and_replays()


    def test_actual_assist_helper_sacrifice_replacement_resumes_and_replays(self):
        session = self.session(741106)
        engine = session.engine
        spell = self.add(engine, "Bring Down", zone="hand")
        cantor = self.add(engine, "Wild Cantor", seat="B")
        target = self.add(engine, "Ancient Brontodon", seat="C")
        self.add(engine, "Dauthi Voidwalker", seat="C", ref="assist-void-c")
        self.add(engine, "Dauthi Voidwalker", seat="D", ref="assist-void-d")
        self.assert_normal_admission(session, spell)
        self.assert_normal_admission(session, cantor)
        self.prepare(session, mana={"W": 1, "C": 2})
        self.checkpoint(session)
        action = self._cast_offer(session, spell)
        self._act(session, "A", action["id"], targets=[target.ref], assist_helper="B")
        self._plan_mana(session, "B", cantor, "U")
        self.assertIsNotNone(self._frame(session, "helper_mana").pending)
        self.assertEqual("battlefield", session.state.cards[cantor.object_id].zone)
        options = session.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        session = self.replay(session, load=True)
        self._act(session, "B", "choose", replacement=options[0]["id"])
        self._act(session, "B", "assist:finish-mana")
        self._act(session, "A", "assist:finish-mana")
        self._act(session, "B", "assist:contribute", assist_amount=1)
        self._act(session, "A", "assist:pay")
        self.assertEqual("exile", session.state.cards[cantor.object_id].zone)
        self.assertEqual(1, session.state.cards[cantor.object_id].counters["void"])
        self.assertEqual(0, sum(session.state.players["B"].mana_pool.values()))
        resolved = self.resolve(session)
        self.assertEqual("replacement.order", resolved.kind)
        self.assertEqual(["A"], resolved.actors)
        choice = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
        self._act(session, "A", "choose", replacement=choice)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[spell.object_id].zone)
        self.assertEqual("exile", session.state.cards[target.object_id].zone)
        self.replay(session, load=True)


    def test_actual_out_of_bounds_assist_keeps_the_announced_spell_target(self):
        session = self.session(741107)
        engine = session.engine
        counter = self.add(engine, "Out of Bounds", zone="hand")
        original = self.add(engine, "Generic Bound Growth", seat="B", zone="hand")
        body = self.add(engine, "Generic Bound Body", seat="B")
        self.assert_normal_admission(session, counter)
        session.state.players["A"].mana_pool.update({"U": 1, "C": 2})
        session.state.players["C"].mana_pool["C"] = 1
        self.prepare(session, actor="B", active="B", mana={"G": 1})
        self.checkpoint(session)
        actions = session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row.get("card") == original.ref)
        self._act(session, "B", action["id"], targets=[body.ref])
        original_ref = session.state.stack[-1].ref
        for _ in range(4):
            if session.pending_principals() == ["pilot:A"]:
                break
            self._act(session, session.pending_principals()[0].split(":")[1], "pass")
        self.assertEqual(["pilot:A"], session.pending_principals())
        self._act(session, "A", self._cast_offer(session, counter)["id"], targets=[original_ref], assist_helper="C")
        self._act(session, "C", "assist:finish-mana")
        self._act(session, "A", "assist:finish-mana")
        self._act(session, "C", "assist:contribute", assist_amount=1)
        self.assertEqual([original_ref], [item.ref for item in session.state.stack])
        self._act(session, "A", "assist:pay")
        self.assertEqual([original_ref], session.state.stack[-1].targets)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[original.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[counter.object_id].zone)
        self.assertEqual("B", session.state.active_player)
        self.replay(session, load=True)
