"""Original public graveyard returns, simultaneous Aura choices, and entry."""

from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_ability_source_counter_targets as native


class PublicGraveyardEntryRuntimeTests(unittest.TestCase):
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
    private_replay = native.AbilitySourceCounterRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "public-graveyard-entry.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/public-graveyard-entry-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public graveyard entries", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def assert_ready(self, session, cards):
        for card in cards:
            binding = self.binding(session, card)
            self.assertTrue(binding["strict_capability_ready"], (card.printed_name, binding["blockers"]))

    def test_original_open_vaults_orders_owner_aura_choices_before_simultaneous_entry(self):
        session = self.session(980_001)
        source = self.add(session.engine, "Open the Vaults", zone="hand")
        flash = self.add(session.engine, "Vedalken Orrery")
        replacement = self.add(session.engine, "Root Maze")
        hexproof = self.add(session.engine, "Slippery Bogle", seat="C")
        protected = self.add(session.engine, "White Knight", seat="C")
        worm = self.add(session.engine, "Craw Wurm", seat="B")
        private = self.add(session.engine, "Generic Bound Growth", seat="C", zone="hand", ref="public-entry-private-C")
        red = self.add(session.engine, "Rancor", zone="graveyard")
        black = self.add(session.engine, "Dead Weight", seat="B", zone="graveyard")
        artifact_a = self.add(session.engine, "Sol Ring", zone="graveyard", ref="public-entry-artifact-A")
        artifact_b = self.add(session.engine, "Sol Ring", seat="B", zone="graveyard", ref="public-entry-artifact-B")
        anthem = self.add(session.engine, "Glorious Anthem", seat="B", zone="graveyard")
        arriving_creature = self.add(session.engine, "Nyxborn Courser", zone="graveyard")
        returning = (red, black, artifact_a, artifact_b, anthem, arriving_creature)
        self.assert_ready(session, (source, flash, replacement, hexproof, protected, worm, *returning))
        self.prepare(session, active="B", mana={"C": 10, "W": 10})
        self.checkpoint(session)
        self.cast(session, source)
        for seat, aura, target in (("B", black, worm), ("A", red, hexproof)):
            self.assertEqual("aura.entry", self.settle(session).kind)
            self.assertEqual([seat], session.state.pending_decision.actors)
            for viewer in "ABCD":
                if viewer != seat:
                    self.assertIsNone(session.packet("pilot:" + viewer, full=True)["decision"])
            self.assertTrue(all(session.state.cards[card.object_id].zone == "graveyard" for card in returning))
            legal = session.packet("pilot:" + seat, full=True)["decision"]["ctx"]["target_schema"]["legal_refs"]
            self.assertIn(hexproof.ref, legal)
            self.assertNotIn(arriving_creature.ref, legal)
            if seat == "B":
                self.assertNotIn(protected.ref, legal)
                self.reject(session, {"action_id": "choose", "aura_target": protected.ref}, seat=seat)
            self.reject(session, {"action_id": "choose", "aura_target": arriving_creature.ref}, seat=seat)
            session = self.private_replay(session, private)
            command = {"action_id": "choose", "aura_target": target.ref}
            self.reject(session, command, seat="D")
            result = session.act("pilot:" + seat, command)
            self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.settle(session))
        for card in returning[:-1]:
            current = session.state.cards[card.object_id]
            self.assertEqual(("battlefield", card.owner), (current.zone, current.controller))
        self.assertEqual("battlefield", session.state.cards[arriving_creature.object_id].zone)
        self.assertTrue(all(session.state.cards[card.object_id].tapped for card in (artifact_a, artifact_b)))
        self.assertTrue(all(not session.state.cards[card.object_id].tapped for card in (red, black, anthem, arriving_creature)))
        self.assertEqual(hexproof.object_id, session.state.cards[red.object_id].attached_to)
        self.assertEqual(worm.object_id, session.state.cards[black.object_id].attached_to)
        self.assertEqual((3, 1), (session.engine._numeric_stat(hexproof.object_id, "power"),
            session.engine._numeric_stat(hexproof.object_id, "toughness")))
        self.assertEqual((5, 3), (session.engine._numeric_stat(worm.object_id, "power"),
            session.engine._numeric_stat(worm.object_id, "toughness")))
        self.assertEqual("B", session.state.active_player)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.private_replay(session, private)

    def test_original_replenish_keeps_aura_without_preexisting_enchantable_object(self):
        session = self.session(980_002)
        source = self.add(session.engine, "Replenish", zone="hand")
        aura = self.add(session.engine, "Rancor", zone="graveyard")
        creature = self.add(session.engine, "Nyxborn Courser", zone="graveyard")
        artifact = self.add(session.engine, "Sol Ring", zone="graveyard")
        self.assert_ready(session, (source, aura, creature, artifact))
        self.prepare(session, mana={"C": 10, "W": 10})
        self.checkpoint(session)
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.assertEqual("battlefield", session.state.cards[creature.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[aura.object_id].zone)
        self.assertIsNone(session.state.cards[aura.object_id].attached_to)
        self.assertEqual("graveyard", session.state.cards[artifact.object_id].zone)
        self.assertFalse(session.state.stack)
        self.replay(session, load=True)

    def test_original_raise_the_past_respects_fixed_mana_value_card_set(self):
        session = self.session(980_003)
        source = self.add(session.engine, "Raise the Past", zone="hand")
        small = self.add(session.engine, "Youthful Knight", zone="graveyard")
        large = self.add(session.engine, "Centaur Courser", zone="graveyard")
        foreign = self.add(session.engine, "Grizzly Bears", seat="B", zone="graveyard")
        land = self.add(session.engine, "Forest", zone="graveyard")
        self.assert_ready(session, (source, small, large, foreign, land))
        self.prepare(session, mana={"C": 10, "W": 10})
        self.checkpoint(session)
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.assertEqual("battlefield", session.state.cards[small.object_id].zone)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "graveyard"
            for card in (large, foreign, land)))
        self.replay(session, load=True)
