"""Source incarnation witnesses for the controller's return choice."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class EntryReturnSourceIncarnationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-return-incarnation.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/combat-defending-player-cards.json",
            ROOT / "tests/fixtures/entry-return-source-incarnation-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Entry return incarnation",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def choose_return(self, session, expected):
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        result = session.act("pilot:A", {"action_id": "choose", "choice": "return"})
        self.assertTrue(result.ok, result.summary)
        decision = session.packet("pilot:A", full=True)["decision"]
        self.assertEqual("choice.apnap", decision["kind"])
        self.assertEqual([expected.ref], decision["ctx"]["options"])

    def test_actual_quickling_can_return_the_new_incarnation_for_its_earlier_trigger(self):
        session = self.session(400711807)
        source = self.add(session.engine, "Quickling", zone="hand")
        blink = self.add(session.engine, "Cloudshift", zone="hand")
        bear = self.add(session.engine, "Grizzly Bears")
        self.assert_closed(session, source)
        self.assert_closed(session, blink)
        self.prepare(session, mana={"C": 1, "U": 1, "W": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", source, reserve={"W": 1})
        self.resolve_top(session)
        original = session.state.stack[-1]
        original_incarnation = original.context["source_logical_object_id"]

        self.cast(session, "A", blink, [source.ref])
        self.resolve_top(session)
        new = session.state.stack[-1]
        current = session.state.cards[source.object_id]
        self.assertEqual(current.logical_object_id, new.context["source_logical_object_id"])
        self.assertNotEqual(original_incarnation, current.logical_object_id)

        self.choose_return(session, bear)
        result = session.act("pilot:A", {"action_id": "choose", "cards": [bear.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", session.state.cards[bear.object_id].zone)
        self.assertEqual(original.ref, session.state.stack[-1].ref)
        self.choose_return(session, source)
        for seat in "BCD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        before = authoritative_state_hash(session.state)
        for seat, target in (("B", source), ("A", bear)):
            result = session.act(f"pilot:{seat}", {"action_id": "choose", "cards": [target.ref]})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "cards": [source.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_actual_quickling_resolves_the_sacrifice_when_return_is_unavailable(self):
        session = self.session(118124001)
        source = self.add(session.engine, "Quickling", zone="hand")
        foreign = self.add(session.engine, "Grizzly Bears", seat="B")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 1, "U": 1})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.resolve_top(session)
        self.assertEqual("battlefield", session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[foreign.object_id].zone)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "choose", "choice": "return"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.replay(session, load=True)
