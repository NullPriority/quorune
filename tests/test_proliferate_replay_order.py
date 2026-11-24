"""Native replay retains the order of a versioned Proliferate choice."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.counter_placement_event_model import PROLIFERATE_ORDER_VERSION_FIELD
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class OriginalProliferateReplayOrderTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "proliferate-order.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/proliferate-replay-order-cards.json"], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Proliferate order", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_actual_thrummingbird_choice_order_survives_checkpoint_and_resume(self):
        game = self.session(70134091)
        bird = self.add(game.engine, "Thrummingbird")
        bird.acquired_control_turn_count = -1
        self.assert_closed(game, bird)
        first = self.add(game.engine, "Island", seat="B", ref="z-countered-permanent")
        second = self.add(game.engine, "Island", seat="C", ref="a-countered-permanent")
        for permanent in (first, second):
            permanent.counters["charge"] = 1
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(bird, "B")])
        self.until(game, lambda current: self.trigger_count(current, bird) == 1)
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        packet = game.packet("pilot:A", full=True)["decision"]
        refs = tuple(packet["legal_actions"][0]["choice_schema"]["legal_refs"])
        self.assertEqual((second.ref, first.ref), refs)
        self.assertEqual(1, pending.continuation["effect"][PROLIFERATE_ORDER_VERSION_FIELD])
        restored = self.replay(game, load=True)
        packet = restored.packet("pilot:A", full=True)["decision"]
        self.assertEqual(refs, tuple(packet["legal_actions"][0]["choice_schema"]["legal_refs"]))
        action = restored.act("pilot:A", {"action_id": "choose", "objects": [first.ref, second.ref]})
        self.assertTrue(action.ok, action.summary)
        for permanent in (first, second):
            self.assertEqual(2, restored.state.cards[permanent.object_id].counters["charge"])
        self.replay(restored, load=True)
