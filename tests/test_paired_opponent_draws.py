"""Paired draws retain APNAP ordering, one target, and private card identities."""

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_ability_source_counter_targets as native


class PairedOpponentDrawRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.AbilitySourceCounterRuntimeTests.session
    add = native.AbilitySourceCounterRuntimeTests.add
    prepare = native.AbilitySourceCounterRuntimeTests.prepare
    checkpoint = native.AbilitySourceCounterRuntimeTests.checkpoint
    priority = native.AbilitySourceCounterRuntimeTests.priority
    actions = native.AbilitySourceCounterRuntimeTests.actions
    cast = native.AbilitySourceCounterRuntimeTests.cast
    binding = native.AbilitySourceCounterRuntimeTests.binding
    reject = native.AbilitySourceCounterRuntimeTests.reject
    settle = native.AbilitySourceCounterRuntimeTests.settle
    replay = native.AbilitySourceCounterRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "paired-draws.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/paired-opponent-draw-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paired draws", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_original_draws_order_privacy_and_sole_opponent_target_revalidation(self):
        for index, (name, active, count, target_leaves) in enumerate((
            ("Secret Rendezvous", "A", 3, False),
            ("Farsight Adept", "B", 1, False),
            ("Secret Rendezvous", "A", 3, True),
        )):
            with self.subTest(name=name, active=active, target_leaves=target_leaves):
                session = self.session(978_000 + index)
                source = self.add(session.engine, name, zone="hand")
                if active == "B":
                    flash = self.add(session.engine, "Vedalken Orrery")
                    self.assertTrue(self.binding(session, flash)["strict_capability_ready"])
                self.assertTrue(self.binding(session, source)["strict_capability_ready"])
                self.prepare(session, active=active, mana={"C": 2, "W": 2})
                self.checkpoint(session)
                row = next(row for row in self.actions(session) if row.get("card") == source.ref)
                if name == "Secret Rendezvous":
                    self.reject(session, {"action_id": row["id"], "targets": ["A"], "pay": "auto"})
                self.cast(session, source, ["C"] if name == "Secret Rendezvous" else (),
                    payment={"C": 1, "W": 2} if name == "Secret Rendezvous" else {"C": 2, "W": 1})
                if name == "Farsight Adept":
                    self.assertEqual("semantic.target", self.settle(session).kind)
                    schema = session.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
                    self.assertEqual({"B", "C", "D"}, set(schema["legal_refs"]))
                    self.reject(session, {"action_id": "choose", "targets": ["A"]})
                    session = self.replay(session, load=True)
                    command = {"action_id": "choose", "targets": ["C"]}
                    self.reject(session, command, seat="D")
                    result = session.act("pilot:A", command)
                    self.assertTrue(result.ok, result.summary)
                else:
                    session = self.replay(session, load=True)
                hands = {seat: set(player.zones["hand"]) for seat, player in session.state.players.items()}
                tops = {seat: list(reversed(player.zones["library"][-count:]))
                    for seat, player in session.state.players.items()}
                event_before = max(event.event_id for event in session.state.events)
                if target_leaves:
                    self.priority(session, "C")
                    result = session.act("pilot:C", {"action_id": "concede", "confirm_concede": True})
                    self.assertTrue(result.ok, result.summary)
                    session = self.replay(session, load=True)
                self.assertIsNone(self.settle(session))
                draws = [event.actor for event in session.state.events
                    if event.event_id > event_before and event.code == "card.draw"]
                expected = [] if target_leaves else (["A"] * count + ["C"] * count
                    if active == "A" else ["C", "A"])
                self.assertEqual(expected, draws)
                for seat in "ABD" if target_leaves else "ABCD":
                    gained = set(session.state.players[seat].zones["hand"]) - hands[seat]
                    self.assertEqual(set(tops[seat]) if not target_leaves and seat in "AC" else set(), gained)
                    for object_id in gained:
                        ref = session.state.cards[object_id].ref
                        for viewer in "ABCD":
                            rendered = json.dumps(session.packet("pilot:" + viewer, full=True))
                            if viewer == seat:
                                self.assertIn(ref, rendered)
                            else:
                                self.assertNotIn(ref, rendered)
                self.assertEqual(active, session.state.active_player)
                self.assertFalse(session.state.stack)
                self.assertEqual("battlefield" if name == "Farsight Adept" else "graveyard",
                    session.state.cards[source.object_id].zone)
                self.replay(session, load=True)
