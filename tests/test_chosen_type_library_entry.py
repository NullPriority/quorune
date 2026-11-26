"""Native chosen-type reveal prefix, entry, and shuffle."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.chosen_type_library_entry import CHOSEN_TYPE_LIBRARY_ENTRY_CAPABILITY
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database


class ChosenTypeLibraryEntryTests(InheritedNativeFixture, unittest.TestCase):
    source = "Underflow Surveyor"
    profile = "commander_review"

    @classmethod
    def use_database(cls, database, land):
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Chosen-type entry", [
            DeckEntry(commander, 1, "commander"), DeckEntry(land, 30),
        ], [commander])

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        database = Path(cls.temp.name) / "chosen-type-entry.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "activation-result-views-cards.json",
            fixtures / "chosen-type-library-entry-cards.json",
        ], database)
        cls.use_database(database, "Generic Bound Plains")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def activate(self, session, source):
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 2, "U": 2})
        self.checkpoint(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        accepted = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, list(decision.actors)))

    def choose_type(self, session, value, *, actor="A"):
        return session.act(f"pilot:{actor}", {"action_id": "choose", "creature_type": value})

    def test_paid_source_stays_departed_while_the_first_match_enters_before_shuffle(self):
        session = self.session(701240801)
        source = self.add(session.engine, self.source)
        hidden = self.add(session.engine, "Grizzly Bears", zone="library", ref="deeper-unrevealed-bear")
        matching = self.add(session.engine, "Grizzly Bears", zone="library", ref="first-revealed-bear")
        second = self.add(session.engine, "Ornithopter", zone="library")
        first = self.add(session.engine, "Sol Ring", zone="library")
        self.activate(session, source)
        before = authoritative_state_hash(session.state)
        self.assertFalse(self.choose_type(session, "Bear", actor="B").ok)
        self.assertFalse(self.choose_type(session, "Invalid Unpinned Creature Type").ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        original = ZoneTransitionOwner.shuffle_library
        order = []

        def observed_shuffle(owner, seat, **kwargs):
            if seat == "A":
                order.append(owner.state.cards[matching.object_id].zone)
            return original(owner, seat, **kwargs)

        with patch.object(ZoneTransitionOwner, "shuffle_library", observed_shuffle):
            result = self.choose_type(session, "Bear")
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["battlefield"], order)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual("library", session.state.cards[hidden.object_id].zone)
        event = next(row for row in reversed(session.state.events) if row.code == "library.selection")
        self.assertEqual([first.ref, second.ref, matching.ref], event.details["objects"])
        self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:B", full=True)))
        for card in (first, second):
            self.assertEqual([], session.state.cards[card.object_id].revealed_to)
        self.replay(session, load=True)

    def test_no_match_reveals_and_shuffles_the_whole_library_without_entry(self):
        session = self.session(701240802)
        source = self.add(session.engine, self.source)
        before = set(session.state.players["A"].zones["library"])
        self.activate(session, source)
        result = self.choose_type(session, "Bear")
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before, set(session.state.players["A"].zones["library"]))
        event = next(row for row in reversed(session.state.events) if row.code == "library.selection")
        self.assertEqual((len(before), 0), (event.details["looked_count"], event.details["selected_count"]))
        self.assertEqual(len(before), len(event.details["objects"]))
        self.assertTrue(any(row.code == "library.shuffle" for row in session.state.events))
        self.replay(session, load=True)

    def test_omitting_the_entry_and_shuffle_intents_is_killed(self):
        with (patch("quorune.rules.chosen_type_library_entry.chosen_type_library_entry_intents", return_value=()),
              self.assertRaisesRegex(AssertionError, "battlefield")):
            self.test_paid_source_stays_departed_while_the_first_match_enters_before_shuffle()

    def test_choice_replacement_and_authority_withdrawal_leave_a_material_residual(self):
        card = self.db.lookup(self.source, fuzzy=False)
        compiled = compile_oracle_card(card, capability_registry=self.registry, capability_profile=self.profile)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        self.assertIn(CHOSEN_TYPE_LIBRARY_ENTRY_CAPABILITY, compiled.faces[0].nodes[0].capability_dependencies)
        changed = replace(card, oracle_text=card.oracle_text.replace("a creature card of that type",
                                                                  "two cards of that type"))
        self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                       capability_profile=self.profile).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == CHOSEN_TYPE_LIBRARY_ENTRY_CAPABILITY).update(
            status="blocked", blockers=["chosen-type entry authority withdrawn"])
        registry = CapabilityRegistry(raw)
        registry.mark_evidence_verified(self.registry.evidence_fingerprint)
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry,
                                                       capability_profile=self.profile).status)
