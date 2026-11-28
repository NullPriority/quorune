"""Printed controller-first contributions and CR 121.2c private draws."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT, packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.mana_pool_payment import PoolManaUnit
from scripts.build_test_database import build_fixture_database


class ManaContributionDrawNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "mana-contribution-draw.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/fixed-self-cost-surface-cards.json",
            ROOT / "tests/fixtures/paired-opponent-draw-cards.json",
            ROOT / "tests/fixtures/mana-contribution-draw-cards.json",
            ROOT / "tests/fixtures/state-based-destination-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Mana contribution draw", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def start(self, seed, *, active="A", controller="A", pools, mana_sources=(), restricted_mana=None):
        session = self.session(seed)
        spell = self.add(session.engine, "Minds Aglow", seat=controller, zone="hand")
        self.assert_closed(session, spell)
        if active != controller:
            timing = self.add(session.engine, "Vedalken Orrery", seat=controller)
            self.assert_closed(session, timing)
        lands = {
            seat: self.add(session.engine, "Generic Bound Plains", seat=seat,
                           ref=f"contribution-source-{seat}") for seat in mana_sources
        }
        for seat, pool in pools.items():
            session.state.players[seat].mana_pool.update(pool)
        if restricted_mana is not None:
            session.engine._add_mana_to_pool(
                controller, {restricted_mana.color: 1}, restriction=restricted_mana.restriction,
            )
        self.prepare(session, active=active)
        self.checkpoint(session)
        self.cast_for(session, controller, spell)
        self.assertEqual("priority", session.state.pending_decision.kind)
        self.assertEqual(controller, session.state.stack[-1].controller)
        self.assertEqual(0, session.state.players[controller].mana_pool["U"])
        return session, spell, lands

    def begin_resolution(self, session, actor):
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        return self.choice(session, actor)

    def choice(self, session, actor):
        self.assertEqual([f"pilot:{actor}"], session.pending_principals())
        decision = session.packet(f"pilot:{actor}", full=True)["decision"]
        self.assertTrue(decision["cap"])
        self.assertEqual("semantic.choice", decision["kind"])
        self.assertEqual("mana_contribution_draw", decision["ctx"]["operation"])
        for seat in "ABCD":
            if seat != actor:
                self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        return decision

    def contribute(self, session, actor, amount, **selectors):
        result = session.act(f"pilot:{actor}", {"action_id": "choose", "x": amount, **selectors})
        self.assertTrue(result.ok, result.summary)

    def mana_action(self, decision, *, symbol=None):
        action, = (row for row in decision["ctx"]["legal_actions"]
                   if row["id"].startswith("mana-contribution:mana:")
                   and (symbol is None or symbol in row["label"]))
        return action

    def activate_mana(self, session, actor, action):
        result = session.act(f"pilot:{actor}", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)

    def assert_rejected(self, session, actor, command, *, pending_actor):
        before = authoritative_state_hash(session.state)
        accepted_commands = len(session.commands)
        context = self.choice(session, pending_actor)["ctx"]
        result = session.act(f"pilot:{actor}", command)
        self.assertFalse(result.ok, result.summary)
        self.assertFalse(result.state_changed)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual(accepted_commands, len(session.commands))
        self.assertEqual(context, self.choice(session, pending_actor)["ctx"])

    def hands(self, session):
        return {seat: tuple(player.zones["hand"]) for seat, player in session.state.players.items()}

    def draws_since(self, session, start):
        return [event.actor for event in session.state.events[start:] if event.code == "card.draw"]

    def test_minds_aglow_uses_controller_first_payments_and_apnap_draws(self):
        session, spell, _ = self.start(121_608_201, active="B", controller="C", pools={
            "A": {"G": 1}, "B": {"R": 2}, "C": {"U": 1, "C": 2}, "D": {"W": 1},
        })
        hands = self.hands(session)
        first_event = len(session.state.events)
        self.begin_resolution(session, "C")
        observed = []
        for seat, amount in (("C", 2), ("D", 0), ("A", 1), ("B", 1)):
            decision = self.choice(session, seat)
            self.assertEqual(observed, decision["ctx"]["contributions"])
            self.assertEqual(hands, self.hands(session))
            self.assertEqual([], self.draws_since(session, first_event))
            self.assertEqual("stack", session.state.cards[spell.object_id].zone)
            self.contribute(session, seat, amount)
            observed.append({"player": seat, "amount": amount})

        self.assertEqual([seat for seat in "BCDA" for _ in range(4)],
                         self.draws_since(session, first_event))
        self.assertEqual({seat: len(before) + 4 for seat, before in hands.items()},
                         {seat: len(after) for seat, after in self.hands(session).items()})
        self.assertEqual({"A": 0, "B": 1, "C": 0, "D": 1}, {
            seat: sum(player.mana_pool.values()) for seat, player in session.state.players.items()
        }, "Each contribution must actually spend its payer's mana")
        self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
        self.assertFalse(session.state.stack)
        self.assertEqual("priority", session.state.pending_decision.kind)

    def test_minds_aglow_rejects_foreign_and_unaffordable_payments_then_resumes(self):
        restricted = PoolManaUnit("G", restriction="creature_spell_only")
        session, _, lands = self.start(121_608_202, pools={
            "A": {"U": 1, "W": 1, "R": 1}, "B": {"C": 3}, "C": {"G": 1}, "D": {"R": 1},
        }, mana_sources=("A", "B"), restricted_mana=restricted)
        first = self.begin_resolution(session, "A")
        self.assert_rejected(session, "B", {
            "action_id": "choose", "x": 1, "cap": first["cap"],
        }, pending_actor="A")
        mana_action = self.mana_action(first)
        self.assert_rejected(session, "B", {"action_id": mana_action["id"]}, pending_actor="A")
        self.assert_rejected(session, "A", {"action_id": "pass"}, pending_actor="A")
        for fields in (
            {"x": 3},
            {"x": 4},
            {"x": -1},
            {"x": True},
            {"x": "1"},
            {"x": 1, "player": "B"},
            {"x": 1, "mana_pool_payment": {PoolManaUnit("C").key: 1}},
            {"x": 1, "mana_pool_payment": {restricted.key: 1}},
        ):
            with self.subTest(fields=fields):
                self.assert_rejected(session, "A", {"action_id": "choose", **fields},
                                     pending_actor="A")

        self.activate_mana(session, "A", mana_action)
        self.assertTrue(session.state.cards[lands["A"].object_id].tapped)
        self.assertFalse(session.state.cards[lands["B"].object_id].tapped)
        self.assertEqual(2, session.state.players["A"].mana_pool["W"])
        self.assertEqual([], self.choice(session, "A")["ctx"]["contributions"])
        session = self.replay(session, load=True)
        pool_rows = {row["color"]: row for row in self.choice(session, "A")["ctx"]["mana_pool_rows"]}
        self.assertEqual({"W": 2, "R": 1}, {color: row["maximum"] for color, row in pool_rows.items()})
        white = pool_rows["W"]["id"]
        red = pool_rows["R"]["id"]
        self.assert_rejected(session, "A", {
            "action_id": "choose", "x": 2, "mana_pool_payment": {white: 1},
        }, pending_actor="A")
        self.contribute(session, "A", 2, mana_pool_payment={white: 1, red: 1})
        self.assertEqual(1, session.state.players["A"].mana_pool["W"])
        self.assertEqual(0, session.state.players["A"].mana_pool["R"])
        self.assertEqual(1, session.engine._restricted_mana("A")[restricted.restriction][restricted.color])
        self.assertEqual(3, session.state.players["B"].mana_pool["C"])
        self.contribute(session, "B", 0)
        self.contribute(session, "C", 0)
        self.contribute(session, "D", 0)
        self.replay(session, load=True)

    def test_minds_aglow_all_zero_contributions_draw_nothing(self):
        session, spell, lands = self.start(121_608_203, pools={
            "A": {"U": 1, "G": 1}, "B": {"R": 1}, "C": {"C": 1}, "D": {"W": 1},
        }, mana_sources=("C",))
        hands = self.hands(session)
        first_event = len(session.state.events)
        self.begin_resolution(session, "A")
        self.contribute(session, "A", 0)
        self.contribute(session, "B", 0)
        self.activate_mana(session, "C", self.mana_action(self.choice(session, "C")))
        self.contribute(session, "C", 0)
        self.contribute(session, "D", 0)
        self.assertEqual(hands, self.hands(session))
        self.assertEqual([], self.draws_since(session, first_event))
        self.assertTrue(session.state.cards[lands["C"].object_id].tapped)
        self.assertEqual({"C": 1, "W": 1}, {color: count for color, count in
                         session.state.players["C"].mana_pool.items() if count})
        self.assertEqual([1, 1, 2, 1], [sum(session.state.players[seat].mana_pool.values())
                                       for seat in "ABCD"])
        self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
        self.assertFalse(session.state.stack)
        self.assertEqual("priority", session.state.pending_decision.kind)

    def test_minds_aglow_keeps_draws_private_through_exact_replay(self):
        session, spell, _ = self.start(121_608_204, pools={
            "A": {"U": 1, "G": 1}, "B": {"R": 1}, "C": {"C": 1}, "D": {"W": 1},
        })
        hands = self.hands(session)
        tops = {seat: player.zones["library"][-1] for seat, player in session.state.players.items()}
        self.begin_resolution(session, "A")
        self.contribute(session, "A", 1)
        before = packet_without_decision_capability(session.packet("pilot:B", full=True))["decision"]
        session = self.replay(session, load=True)
        after = packet_without_decision_capability(session.packet("pilot:B", full=True))["decision"]
        self.assertEqual(before, after)
        for seat in "BCD":
            self.contribute(session, seat, 0)
        self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
        for seat, prior in hands.items():
            self.assertEqual({tops[seat]}, set(self.hands(session)[seat]) - set(prior))
        for current in (session, self.replay(session, load=True)):
            for viewer in "ABCD":
                packet = packet_without_decision_capability(current.packet(f"pilot:{viewer}", full=True))
                rendered = json.dumps(packet)
                for owner, object_id in tops.items():
                    card = current.state.cards[object_id]
                    if viewer == owner:
                        self.assertIn(card.ref, rendered)
                    else:
                        self.assertNotIn(card.ref, rendered)

    def test_minds_aglow_replays_mana_activation_replacement(self):
        session = self.session(121_608_206)
        spell = self.add(session.engine, "Minds Aglow", zone="hand")
        cantor = self.add(session.engine, "Wild Cantor", seat="B")
        self.add(session.engine, "Dauthi Voidwalker", seat="C", ref="contribution-voidwalker-c")
        self.add(session.engine, "Dauthi Voidwalker", seat="D", ref="contribution-voidwalker-d")
        self.assert_closed(session, spell)
        self.prepare(session, mana={"U": 1, "W": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", spell)
        self.begin_resolution(session, "A")
        self.contribute(session, "A", 0)
        self.activate_mana(session, "B", self.mana_action(self.choice(session, "B"), symbol="{U}"))

        self.assertEqual(["pilot:B"], session.pending_principals())
        self.assertEqual("battlefield", session.state.cards[cantor.object_id].zone)
        self.assertEqual(0, session.state.players["B"].mana_pool["U"])
        options = session.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        session = self.replay(session, load=True)
        self.assertEqual(options, session.packet("pilot:B", full=True)["decision"]["ctx"]["options"])
        result = session.act("pilot:B", {"action_id": "choose", "replacement": options[0]["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([{"player": "A", "amount": 0}], self.choice(session, "B")["ctx"]["contributions"])
        self.assertEqual("exile", session.state.cards[cantor.object_id].zone)
        self.assertEqual(1, session.state.cards[cantor.object_id].counters["void"])
        self.assertEqual(1, session.state.players["B"].mana_pool["U"])
        self.contribute(session, "B", 1)
        self.assertEqual(0, session.state.players["B"].mana_pool["U"])
        self.choice(session, "C")
        self.replay(session, load=True)

    def test_omitting_real_mana_deduction_is_detected(self):
        session, _, _ = self.start(121_608_205, pools={
            "A": {"U": 1, "G": 1}, "B": {"R": 1}, "C": {"C": 1}, "D": {"W": 1},
        })
        self.begin_resolution(session, "A")

        with patch.object(session.engine, "pay_mana_cost_intent") as omit:
            with self.assertRaisesRegex(AssertionError, "The contribution must deduct the payer's mana"):
                self.contribute(session, "A", 1)
                self.assertEqual(0, session.state.players["A"].mana_pool["G"],
                                 "The contribution must deduct the payer's mana")
        omit.assert_called_once()
